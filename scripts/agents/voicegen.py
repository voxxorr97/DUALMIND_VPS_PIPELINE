"""Voicegen agent for the DualMind v2.2 pipeline.

This third pipeline agent reads ready scripts from SQLite, strips Scriptwriter
section markers, generates French mystery narration with ElevenLabs, stores MP3
files under ``output/audio``, and marks scripts as voiced.

Usage examples::

    python scripts/agents/voicegen.py
    python scripts/agents/voicegen.py --limit 1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any, Protocol, TypedDict

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "dualmind.db"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "output" / "audio"
LOG_PATH = REPO_ROOT / "logs" / "voicegen.log"
DOTENV_PATH = REPO_ROOT / ".env"
MODEL_ID = "eleven_multilingual_v2"
DEFAULT_LIMIT = 3
MAX_LIMIT = 3
REQUEST_TIMEOUT_SECONDS = 120
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
VOICE_SETTINGS = {
    "stability": 0.5,
    "similarity_boost": 0.8,
    "style": 0.4,
    "use_speaker_boost": True,
}
SECTION_MARKER_RE = re.compile(
    r"^\s*\[[^\]]+\]\s*(?:\([^)]*\))?\s*:?\s*",
    flags=re.MULTILINE,
)
WHITESPACE_RE = re.compile(r"[ \t]+")


class ReadyScript(TypedDict):
    """Ready script selected from ``scripts_generated``."""

    id: int
    title: str
    script_text: str
    audio_path: str | None


class RunSummary(TypedDict):
    """Structured summary returned by the agent run."""

    ok: bool
    ready_found: int
    audios_created: int
    scripts_voiced: int
    skipped: int
    errors: int


class VoiceClient(Protocol):
    """Minimal text-to-speech client used by the agent and standalone test."""

    def synthesize_to_file(self, text: str, output_path: Path) -> None:
        """Generate speech audio for ``text`` into ``output_path``."""


def configure_logging() -> None:
    """Configure file logging for Voicegen runs."""
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


def resolve_output_dir() -> Path:
    """Return the audio output directory, honoring ``VOICEGEN_OUTPUT_DIR`` if set."""
    configured = os.environ.get("VOICEGEN_OUTPUT_DIR")
    if not configured:
        return DEFAULT_OUTPUT_DIR
    path = Path(configured).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


def get_connection() -> sqlite3.Connection:
    """Open the SQLite database and ensure the Voicegen schema requirements exist."""
    db_path = resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create required tables and add ``audio_path`` to older databases."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS scripts_generated (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER,
            title TEXT NOT NULL,
            script_text TEXT NOT NULL,
            platform TEXT NOT NULL,
            duration_seconds INTEGER,
            status TEXT NOT NULL DEFAULT 'draft',
            audio_path TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(scripts_generated)").fetchall()
    }
    if "audio_path" not in columns:
        connection.execute("ALTER TABLE scripts_generated ADD COLUMN audio_path TEXT")
        connection.commit()


def get_ready_scripts(connection: sqlite3.Connection, limit: int = DEFAULT_LIMIT) -> list[ReadyScript]:
    """Fetch up to ``limit`` scripts that are ready for voice generation."""
    bounded_limit = min(max(limit, 0), MAX_LIMIT)
    rows = connection.execute(
        """
        SELECT id, title, script_text, audio_path
        FROM scripts_generated
        WHERE status = 'ready'
        ORDER BY id ASC
        LIMIT ?
        """,
        (bounded_limit,),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "title": str(row["title"]),
            "script_text": str(row["script_text"]),
            "audio_path": row["audio_path"],
        }
        for row in rows
    ]


def strip_script_markers(script_text: str) -> str:
    """Remove Scriptwriter section markers and normalize narration text."""
    without_markers = SECTION_MARKER_RE.sub("", script_text)
    normalized_lines = [WHITESPACE_RE.sub(" ", line).strip() for line in without_markers.splitlines()]
    return "\n".join(line for line in normalized_lines if line).strip()


def slugify(value: str, fallback: str = "script") -> str:
    """Return a filesystem-safe ASCII slug for a topic or title."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    return slug[:80] or fallback


def build_audio_path(script: ReadyScript, output_dir: Path) -> Path:
    """Build the deterministic MP3 path for one script."""
    topic_slug = slugify(script["title"], fallback=f"script-{script['id']}")
    return output_dir / f"{script['id']}_{topic_slug}.mp3"


class ElevenLabsClient:
    """Small requests-based ElevenLabs text-to-speech client."""

    def __init__(self, api_key: str, voice_id: str) -> None:
        self.api_key = api_key
        self.voice_id = voice_id

    def synthesize_to_file(self, text: str, output_path: Path) -> None:
        """Call ElevenLabs and write the returned MP3 bytes to ``output_path``."""
        response = requests.post(
            ELEVENLABS_API_URL.format(voice_id=self.voice_id),
            headers={
                "Accept": "audio/mpeg",
                "Content-Type": "application/json",
                "xi-api-key": self.api_key,
            },
            json={
                "text": text,
                "model_id": MODEL_ID,
                "voice_settings": VOICE_SETTINGS,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            error_preview = response.text[:500]
            raise RuntimeError(f"ElevenLabs HTTP {response.status_code}: {error_preview}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)


def get_elevenlabs_client() -> VoiceClient:
    """Create a real ElevenLabs client from environment variables."""
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is required in .env or the process environment.")
    if not voice_id:
        raise RuntimeError("ELEVENLABS_VOICE_ID is required in .env or the process environment.")
    return ElevenLabsClient(api_key=api_key, voice_id=voice_id)


def synthesize_with_retry(client: VoiceClient, text: str, output_path: Path) -> None:
    """Generate one audio file with one retry after an API failure."""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            client.synthesize_to_file(text, output_path)
            return
        except Exception as exc:  # noqa: BLE001 - API clients raise heterogeneous exceptions.
            last_error = exc
            logging.warning("ElevenLabs generation failed on attempt %s/2: %s", attempt + 1, exc)
            if attempt == 0:
                time.sleep(1)
    raise RuntimeError(f"ElevenLabs generation failed after retry: {last_error}")


def mark_script_voiced(connection: sqlite3.Connection, script_id: int, audio_path: Path) -> None:
    """Persist the audio path and transition a ready script to voiced."""
    relative_path = audio_path.relative_to(REPO_ROOT) if audio_path.is_relative_to(REPO_ROOT) else audio_path
    with connection:
        connection.execute(
            """
            UPDATE scripts_generated
            SET status = 'voiced', audio_path = ?
            WHERE id = ? AND status = 'ready'
            """,
            (str(relative_path), script_id),
        )


def run_agent(client: VoiceClient | None = None, limit: int = DEFAULT_LIMIT) -> RunSummary:
    """Run Voicegen for up to three ready scripts."""
    bounded_limit = min(max(limit, 0), MAX_LIMIT)
    output_dir = resolve_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    summary: RunSummary = {
        "ok": True,
        "ready_found": 0,
        "audios_created": 0,
        "scripts_voiced": 0,
        "skipped": 0,
        "errors": 0,
    }

    with get_connection() as connection:
        scripts = get_ready_scripts(connection, bounded_limit)
        summary["ready_found"] = len(scripts)
        logging.info("Voicegen run started: %s ready script(s), limit=%s", len(scripts), bounded_limit)

        for script in scripts:
            narration_text = strip_script_markers(script["script_text"])
            if not narration_text:
                summary["errors"] += 1
                summary["skipped"] += 1
                logging.error("Skipping script %s because stripped narration text is empty.", script["id"])
                continue

            audio_path = build_audio_path(script, output_dir)
            try:
                if client is None:
                    load_environment()
                    client = get_elevenlabs_client()
                synthesize_with_retry(client, narration_text, audio_path)
                mark_script_voiced(connection, script["id"], audio_path)
                summary["audios_created"] += 1
                summary["scripts_voiced"] += 1
                logging.info("Generated voice audio for script %s: %s", script["id"], audio_path)
            except Exception as exc:  # noqa: BLE001 - keep agent resilient and continue next script.
                summary["errors"] += 1
                summary["skipped"] += 1
                logging.exception("Skipping script %s after voice generation error: %s", script["id"], exc)

    summary["ok"] = summary["errors"] == 0
    logging.info("Voicegen run finished: %s", summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="Generate ElevenLabs voices for ready DualMind scripts.")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=f"Maximum audios to generate this run, capped at {MAX_LIMIT}.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    configure_logging()
    args = parse_args(argv)
    try:
        summary = run_agent(limit=args.limit)
    except Exception as exc:  # noqa: BLE001 - return JSON failure for schedulers.
        logging.exception("Voicegen fatal error: %s", exc)
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
