"""Scriptwriter agent for the DualMind v2.2 pipeline.

This second pipeline agent reads pending hot topics from SQLite, asks Claude to
write a 60-second French mystery short script, stores generated scripts, records
prompt history, and marks processed topics as scripted.

Usage examples::

    python scripts/agents/scriptwriter.py
    python scripts/agents/scriptwriter.py --limit 2
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Protocol, TypedDict

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "dualmind.db"
LOG_PATH = REPO_ROOT / "logs" / "scriptwriter.log"
DOTENV_PATH = REPO_ROOT / ".env"
NICHE = "Affaires Mystérieuses Non Classées"
MODEL = "claude-sonnet-4-6"
DEFAULT_LIMIT = 3
MAX_LIMIT = 3
PLATFORM = "youtube_shorts_tiktok"
DURATION_SECONDS = 60
PROMPT_TYPE = "scriptwriter_v2.2"
MAX_TOKENS = 1200

PROMPT_TEMPLATE = """Tu es un expert en contenu mystère francophone pour YouTube Shorts et TikTok.
Génère un script de 60 secondes sur ce sujet : {topic_title}
Format strict :
[HOOK] (0-5s) : phrase choc qui accroche immédiatement
[DÉVELOPPEMENT] (5-45s) : 3 faits troublants, style journalistique, rythme rapide
[RÉVÉLATION] (45-55s) : twist ou élément inexpliqué
[CTA] (55-60s) : question ouverte pour engager les commentaires
Langue : français, ton mystérieux et factuel, pas de superlatifs inutiles.
"""


class Topic(TypedDict):
    """Pending topic selected from ``topics_hot``."""

    id: int
    topic: str
    niche: str | None
    viral_score: float


class RunSummary(TypedDict):
    """Structured summary returned by the agent run."""

    ok: bool
    pending_found: int
    scripts_created: int
    prompts_recorded: int
    topics_scripted: int
    skipped: int
    errors: int


class ClaudeClient(Protocol):
    """Minimal Anthropic client protocol used by the agent and tests."""

    messages: Any


def configure_logging() -> None:
    """Configure file logging for Scriptwriter runs."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


def load_environment() -> None:
    """Load local ``.env`` values without overriding exported variables."""
    load_dotenv(DOTENV_PATH)


def resolve_db_path() -> Path:
    """Return the SQLite path, honoring ``SQLITE_DB_PATH`` from the environment."""
    configured = os.environ.get("SQLITE_DB_PATH")
    if not configured:
        return DEFAULT_DB_PATH
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def get_connection() -> sqlite3.Connection:
    """Open the SQLite database and ensure the pipeline schema exists."""
    db_path = resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create required DualMind tables when the database is empty."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS topics_hot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            niche TEXT,
            viral_score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS scripts_generated (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER,
            title TEXT NOT NULL,
            script_text TEXT NOT NULL,
            platform TEXT NOT NULL,
            duration_seconds INTEGER,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (topic_id) REFERENCES topics_hot(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS prompts_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_type TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            output_summary TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def get_pending_topics(connection: sqlite3.Connection, limit: int = DEFAULT_LIMIT) -> list[Topic]:
    """Fetch up to ``limit`` pending topics, highest viral score first."""
    bounded_limit = min(max(limit, 0), MAX_LIMIT)
    rows = connection.execute(
        """
        SELECT id, topic, niche, viral_score
        FROM topics_hot
        WHERE status = 'pending'
        ORDER BY viral_score DESC, id ASC
        LIMIT ?
        """,
        (bounded_limit,),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "topic": str(row["topic"]),
            "niche": row["niche"],
            "viral_score": float(row["viral_score"]),
        }
        for row in rows
    ]


def build_prompt(topic_title: str) -> str:
    """Build the Claude prompt for one topic."""
    return PROMPT_TEMPLATE.format(topic_title=topic_title)


def get_anthropic_client() -> ClaudeClient:
    """Create a real Anthropic client using ``CLAUDE_API_KEY`` from the environment."""
    api_key = os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        raise RuntimeError("CLAUDE_API_KEY is required in .env or the process environment.")

    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is declared in requirements.txt.
        raise RuntimeError("The 'anthropic' package is required. Install it with pip.") from exc

    return anthropic.Anthropic(api_key=api_key)


def extract_response_text(response: Any) -> str:
    """Extract text content from an Anthropic Messages API response."""
    blocks = getattr(response, "content", None)
    if blocks is None and isinstance(response, dict):
        blocks = response.get("content")
    if not blocks:
        return ""

    parts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return "\n".join(parts).strip()


def generate_script_with_retry(client: ClaudeClient, prompt: str) -> str:
    """Generate a script with one retry after an API failure or empty response."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            script_text = extract_response_text(response)
            if not script_text:
                raise RuntimeError("Claude returned an empty script.")
            return script_text
        except Exception as exc:  # noqa: BLE001 - API clients raise heterogeneous exceptions.
            last_error = exc
            logging.warning("Claude generation failed on attempt %s/2: %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(1)
    raise RuntimeError(f"Claude generation failed after retry: {last_error}")


def has_ready_script(connection: sqlite3.Connection, topic_id: int) -> bool:
    """Return True when a ready script already exists for a topic."""
    row = connection.execute(
        """
        SELECT 1
        FROM scripts_generated
        WHERE topic_id = ? AND status = 'ready'
        LIMIT 1
        """,
        (topic_id,),
    ).fetchone()
    return row is not None


def store_script(
    connection: sqlite3.Connection,
    topic: Topic,
    prompt: str,
    script_text: str,
) -> None:
    """Persist the generated script, prompt history, and topic status atomically."""
    output_summary = script_text[:240].replace("\n", " ")
    with connection:
        connection.execute(
            """
            INSERT INTO scripts_generated (
                topic_id, title, script_text, platform, duration_seconds, status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (topic["id"], topic["topic"], script_text, PLATFORM, DURATION_SECONDS, "ready"),
        )
        connection.execute(
            """
            INSERT INTO prompts_history (prompt_type, model, prompt_text, output_summary)
            VALUES (?, ?, ?, ?)
            """,
            (PROMPT_TYPE, MODEL, prompt, output_summary),
        )
        connection.execute(
            """
            UPDATE topics_hot
            SET status = 'scripted'
            WHERE id = ? AND status = 'pending'
            """,
            (topic["id"],),
        )


def mark_topic_scripted(connection: sqlite3.Connection, topic_id: int) -> None:
    """Mark a pending topic as scripted when an existing ready script is found."""
    with connection:
        connection.execute(
            """
            UPDATE topics_hot
            SET status = 'scripted'
            WHERE id = ? AND status = 'pending'
            """,
            (topic_id,),
        )


def run_agent(client: ClaudeClient | None = None, limit: int = DEFAULT_LIMIT) -> RunSummary:
    """Run Scriptwriter for up to three pending topics."""
    bounded_limit = min(max(limit, 0), MAX_LIMIT)

    summary: RunSummary = {
        "ok": True,
        "pending_found": 0,
        "scripts_created": 0,
        "prompts_recorded": 0,
        "topics_scripted": 0,
        "skipped": 0,
        "errors": 0,
    }

    with get_connection() as connection:
        topics = get_pending_topics(connection, bounded_limit)
        summary["pending_found"] = len(topics)
        logging.info("Scriptwriter run started: %s pending topic(s), limit=%s", len(topics), bounded_limit)

        for topic in topics:
            if has_ready_script(connection, topic["id"]):
                mark_topic_scripted(connection, topic["id"])
                summary["topics_scripted"] += 1
                summary["skipped"] += 1
                logging.info("Topic %s already had a ready script; marked scripted.", topic["id"])
                continue

            prompt = build_prompt(topic["topic"])
            try:
                if client is None:
                    load_environment()
                    client = get_anthropic_client()
                script_text = generate_script_with_retry(client, prompt)
                store_script(connection, topic, prompt, script_text)
                summary["scripts_created"] += 1
                summary["prompts_recorded"] += 1
                summary["topics_scripted"] += 1
                logging.info("Generated ready script for topic %s: %s", topic["id"], topic["topic"])
            except Exception as exc:  # noqa: BLE001 - keep agent resilient and continue next topic.
                summary["errors"] += 1
                summary["skipped"] += 1
                logging.exception("Skipping topic %s after generation error: %s", topic["id"], exc)

    summary["ok"] = summary["errors"] == 0
    logging.info("Scriptwriter run finished: %s", summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="Generate scripts for pending DualMind topics.")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum scripts to generate this run, capped at {MAX_LIMIT}.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    configure_logging()
    args = parse_args(argv)
    try:
        summary = run_agent(limit=args.limit)
    except Exception as exc:  # noqa: BLE001 - return JSON failure for schedulers.
        logging.exception("Scriptwriter fatal error: %s", exc)
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
