"""Standalone offline test for the DualMind Voicegen agent.

The test uses a temporary SQLite database and a mock ElevenLabs client, so it
never consumes ElevenLabs credits and does not require API keys.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

TEST_DB_PATH = Path(tempfile.gettempdir()) / "dualmind_voicegen_test.db"
TEST_OUTPUT_DIR = Path(tempfile.gettempdir()) / "dualmind_voicegen_audio"
os.environ["SQLITE_DB_PATH"] = str(TEST_DB_PATH)
os.environ["VOICEGEN_OUTPUT_DIR"] = str(TEST_OUTPUT_DIR)

import voicegen


class MockElevenLabsClient:
    """Mock text-to-speech client that writes an empty MP3 test file."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def synthesize_to_file(self, text: str, output_path: Path) -> None:
        """Record the call and create a deterministic empty MP3 placeholder."""
        self.calls.append({"text": text, "output_path": str(output_path)})
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"")


def reset_test_environment() -> int:
    """Create a fresh temporary database with one ready fake script."""
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()
    if TEST_OUTPUT_DIR.exists():
        for child in TEST_OUTPUT_DIR.iterdir():
            if child.is_file():
                child.unlink()

    with voicegen.get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO scripts_generated (
                topic_id, title, script_text, platform, duration_seconds, status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                "Le dossier impossible du phare abandonné",
                """[HOOK] (0-5s) : Une lumière s'allume chaque nuit dans ce phare officiellement vide.
[DÉVELOPPEMENT] (5-45s) : Les gardiens ont disparu sans laisser de trace. Les registres météo ne mentionnent aucune tempête. Un appel radio a pourtant été capté trois jours plus tard.
[RÉVÉLATION] (45-55s) : Le plus étrange, c'est que la lampe avait été retirée depuis 1978.
[CTA] (55-60s) : Alors, erreur d'archive ou témoin impossible ?""",
                "youtube_shorts_tiktok",
                60,
                "ready",
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def fetch_result(script_id: int) -> dict[str, Any]:
    """Return the updated script row after Voicegen execution."""
    with voicegen.get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, title, status, audio_path
            FROM scripts_generated
            WHERE id = ?
            """,
            (script_id,),
        ).fetchone()
    return dict(row) if row is not None else {}


def assert_expected_results(
    summary: voicegen.RunSummary,
    result: dict[str, Any],
    client: MockElevenLabsClient,
) -> None:
    """Validate that the mock audio path was stored in SQLite."""
    if not summary["ok"]:
        raise AssertionError(f"Agent summary is not ok: {summary}")
    if summary["ready_found"] != 1 or summary["audios_created"] != 1:
        raise AssertionError(f"Unexpected summary counts: {summary}")
    if result.get("status") != "voiced":
        raise AssertionError(f"Expected status voiced, got: {result}")
    audio_path = result.get("audio_path")
    if not audio_path:
        raise AssertionError(f"audio_path was not updated: {result}")
    resolved_audio_path = voicegen.REPO_ROOT / audio_path if not Path(audio_path).is_absolute() else Path(audio_path)
    if not resolved_audio_path.exists():
        raise AssertionError(f"Expected audio file to exist: {resolved_audio_path}")
    if len(client.calls) != 1:
        raise AssertionError(f"Expected one mock ElevenLabs call, got {len(client.calls)}")
    if "[HOOK]" in client.calls[0]["text"] or "[DÉVELOPPEMENT]" in client.calls[0]["text"]:
        raise AssertionError(f"Script markers were not stripped: {client.calls[0]['text']}")


def main() -> int:
    """Run Voicegen with one fake ready script and a mock ElevenLabs client."""
    voicegen.configure_logging()
    script_id = reset_test_environment()
    client = MockElevenLabsClient()

    summary = voicegen.run_agent(client=client, limit=3)
    result = fetch_result(script_id)
    assert_expected_results(summary, result, client)

    print(f"Base SQLite temporaire: {TEST_DB_PATH}")
    print(f"Dossier audio temporaire: {TEST_OUTPUT_DIR}")
    print("\nRésumé agent:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nScript mis à jour:")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\nTexte envoyé au mock ElevenLabs:")
    print(client.calls[0]["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
