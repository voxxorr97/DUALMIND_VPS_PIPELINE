"""Standalone local smoke test for the DualMind Scriptwriter agent."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

TEST_DB_PATH = Path(tempfile.gettempdir()) / "dualmind_scriptwriter_test.db"
os.environ["SQLITE_DB_PATH"] = str(TEST_DB_PATH)

import scriptwriter


class MockClaudeMessages:
    """Mock Anthropic Messages API for deterministic offline tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> SimpleNamespace:
        """Return a Claude-like response without using a real API key."""
        self.calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        topic_title = prompt.split("ce sujet : ", maxsplit=1)[1].split("\n", maxsplit=1)[0]
        script = f"""[HOOK] (0-5s) : Et si {topic_title} cachait encore une zone d'ombre ?
[DÉVELOPPEMENT] (5-45s) : Premier fait : les témoins ne donnent pas la même chronologie. Deuxième fait : une pièce du dossier réapparaît des années plus tard. Troisième fait : les archives publiques contredisent la version officielle.
[RÉVÉLATION] (45-55s) : Le détail le plus troublant, c'est que le dernier indice n'a jamais été expertisé.
[CTA] (55-60s) : Selon vous, simple oubli ou élément volontairement écarté ?"""
        return SimpleNamespace(content=[SimpleNamespace(text=script)])


class MockClaudeClient:
    """Minimal mock client exposing ``messages.create`` like Anthropic."""

    def __init__(self) -> None:
        self.messages = MockClaudeMessages()


def reset_test_database() -> None:
    """Create a fresh temporary SQLite database for the test run."""
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    with scriptwriter.get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO topics_hot (topic, niche, viral_score, status)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    "La disparition inexpliquée du phare de Tévennec",
                    scriptwriter.NICHE,
                    91,
                    "pending",
                ),
                (
                    "Le carnet codé retrouvé dans une affaire classée",
                    scriptwriter.NICHE,
                    88,
                    "pending",
                ),
            ],
        )
        connection.commit()


def fetch_results() -> dict[str, Any]:
    """Return generated rows for formatted assertions and display."""
    with scriptwriter.get_connection() as connection:
        scripts = connection.execute(
            """
            SELECT s.id, s.topic_id, s.title, s.script_text, s.status, t.status AS topic_status
            FROM scripts_generated AS s
            JOIN topics_hot AS t ON t.id = s.topic_id
            ORDER BY s.id ASC
            """
        ).fetchall()
        prompts = connection.execute(
            """
            SELECT id, prompt_type, model, prompt_text, output_summary
            FROM prompts_history
            ORDER BY id ASC
            """
        ).fetchall()
        pending_count = connection.execute(
            "SELECT COUNT(*) AS total FROM topics_hot WHERE status = 'pending'"
        ).fetchone()["total"]

    return {
        "scripts": [dict(row) for row in scripts],
        "prompts": [dict(row) for row in prompts],
        "pending_count": pending_count,
    }


def assert_expected_results(summary: scriptwriter.RunSummary, results: dict[str, Any]) -> None:
    """Validate the smoke-test expectations."""
    if not summary["ok"]:
        raise AssertionError(f"Agent summary is not ok: {summary}")
    if summary["scripts_created"] != 2 or summary["prompts_recorded"] != 2:
        raise AssertionError(f"Unexpected summary counts: {summary}")
    if len(results["scripts"]) != 2:
        raise AssertionError(f"Expected 2 generated scripts, got {len(results['scripts'])}")
    if len(results["prompts"]) != 2:
        raise AssertionError(f"Expected 2 prompt history rows, got {len(results['prompts'])}")
    if results["pending_count"] != 0:
        raise AssertionError(f"Expected no pending topics, got {results['pending_count']}")
    for script in results["scripts"]:
        if script["status"] != "ready" or script["topic_status"] != "scripted":
            raise AssertionError(f"Unexpected script/topic status: {script}")


def main() -> int:
    """Run Scriptwriter with fake topics and a mock Claude client."""
    scriptwriter.configure_logging()
    reset_test_database()
    client = MockClaudeClient()

    summary = scriptwriter.run_agent(client=client, limit=3)
    results = fetch_results()
    assert_expected_results(summary, results)

    print(f"Base SQLite temporaire: {TEST_DB_PATH}")
    print("\nRésumé agent:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nScripts générés:")
    for index, row in enumerate(results["scripts"], start=1):
        print(f"\n{index}. {row['title']} — script={row['status']} topic={row['topic_status']}")
        print(row["script_text"])
    print("\nPrompts enregistrés:")
    for row in results["prompts"]:
        print(f"- #{row['id']} {row['prompt_type']} / {row['model']} / résumé: {row['output_summary'][:80]}")
    print(f"\nAppels mock Claude: {len(client.messages.calls)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
