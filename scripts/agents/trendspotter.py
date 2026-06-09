"""Trendspotter agent for the DualMind v2.2 pipeline.

This agent ingests a Grok-style weekly trends webhook payload, stores raw
trends in SQLite, scores them for the French-speaking "Affaires Mystérieuses
Non Classées" niche, and promotes the five strongest trends to ``topics_hot``.

Usage examples::

    cat payload.json | python scripts/agents/trendspotter.py
    python scripts/agents/trendspotter.py payload.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, TypedDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "dualmind.db"
LOG_PATH = REPO_ROOT / "logs" / "trendspotter.log"
NICHE = "Affaires Mystérieuses Non Classées"
DEFAULT_SOURCE = "grok_weekly_trends"

MYSTERY_KEYWORDS = {
    "affaire",
    "affaires",
    "mystere",
    "mystère",
    "mysterieux",
    "mystérieux",
    "mysterieuse",
    "mystérieuse",
    "enigme",
    "énigme",
    "inexplique",
    "inexpliqué",
    "inexpliquee",
    "inexpliquée",
    "non classe",
    "non classé",
    "non classee",
    "non classée",
    "cold case",
    "disparition",
    "disparu",
    "disparue",
    "crime",
    "criminel",
    "meurtre",
    "assassinat",
    "tueur",
    "victime",
    "suspect",
    "police",
    "enquete",
    "enquête",
    "temoin",
    "témoin",
    "secret",
    "archive",
    "archives",
    "ovni",
    "paranormal",
    "etrange",
    "étrange",
    "surnaturel",
    "conspiration",
    "complot",
}

TREND_LIST_KEYS = ("trends", "weekly_trends", "items", "results", "data")
TITLE_KEYS = ("title", "topic", "name", "query", "headline", "raw_title")
TEXT_KEYS = ("text", "summary", "description", "content", "raw_text")
URL_KEYS = ("url", "link", "source_url", "permalink")
SOURCE_KEYS = ("source", "provider", "origin")


class Trend(TypedDict):
    """Normalized trend record used internally by the agent."""

    source: str
    title: str
    text: str | None
    url: str | None
    score: int


def configure_logging() -> None:
    """Configure file logging for agent runs."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=getattr(logging, os.environ.get("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )


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
    """Open the SQLite database and ensure the required schema exists."""
    db_path = resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    ensure_schema(connection)
    return connection


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create required pipeline tables when the database is empty."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS trends_raw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            raw_title TEXT NOT NULL,
            raw_text TEXT,
            url TEXT,
            collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS topics_hot (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            niche TEXT,
            viral_score REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )


def load_payload(input_path: str | None = None) -> Any:
    """Load JSON from a file path or standard input."""
    if input_path:
        raw_payload = Path(input_path).read_text(encoding="utf-8")
    else:
        raw_payload = sys.stdin.read()
    if not raw_payload.strip():
        raise ValueError("No JSON payload received on stdin or file input.")
    return json.loads(raw_payload)


def find_first_mapping_value(mapping: dict[str, Any], keys: Iterable[str]) -> Any:
    """Return the first value found in a mapping for a list of likely keys."""
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def extract_trend_items(payload: Any) -> list[Any]:
    """Extract trend items from common webhook shapes."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise ValueError("Webhook payload must be a JSON object or array.")

    for key in TREND_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = extract_trend_items(value)
            if nested:
                return nested
    return [payload]


def normalize_text(value: Any) -> str | None:
    """Convert arbitrary JSON values to clean text."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
    return text or None


def normalize_trend(item: Any, fallback_source: str) -> Trend | None:
    """Normalize one raw JSON trend item into the internal trend format."""
    if isinstance(item, str):
        title = normalize_text(item)
        if not title:
            return None
        text = None
        url = None
        source = fallback_source
    elif isinstance(item, dict):
        title = normalize_text(find_first_mapping_value(item, TITLE_KEYS))
        if not title:
            return None
        text = normalize_text(find_first_mapping_value(item, TEXT_KEYS))
        url = normalize_text(find_first_mapping_value(item, URL_KEYS))
        source = normalize_text(find_first_mapping_value(item, SOURCE_KEYS)) or fallback_source
    else:
        return None

    return {
        "source": source,
        "title": title,
        "text": text,
        "url": url,
        "score": score_trend(title, text),
    }


def parse_trends(payload: Any) -> list[Trend]:
    """Parse and score trends from a Grok weekly trends webhook payload."""
    fallback_source = DEFAULT_SOURCE
    if isinstance(payload, dict):
        fallback_source = normalize_text(find_first_mapping_value(payload, SOURCE_KEYS)) or DEFAULT_SOURCE

    trends: list[Trend] = []
    for item in extract_trend_items(payload):
        trend = normalize_trend(item, fallback_source)
        if trend:
            trends.append(trend)
    return trends


def score_trend(title: str, text: str | None = None) -> int:
    """Score a trend from 0 to 100 for mystery/crime/inexplicable potential."""
    searchable = f"{title} {text or ''}".casefold()
    score = 10

    keyword_hits = sum(1 for keyword in MYSTERY_KEYWORDS if keyword in searchable)
    score += min(keyword_hits * 12, 60)

    title_length = len(title)
    if 35 <= title_length <= 95:
        score += 20
    elif 20 <= title_length < 35 or 96 <= title_length <= 130:
        score += 12
    elif title_length > 130:
        score += 4

    digit_groups = re.findall(r"\d+", title)
    if digit_groups:
        score += min(10 + (len(digit_groups) - 1) * 3, 20)

    if "?" in title:
        score += 5

    return max(0, min(score, 100))


def trend_exists(connection: sqlite3.Connection, trend: Trend) -> bool:
    """Return True if a raw trend already exists in the database."""
    row = connection.execute(
        """
        SELECT id FROM trends_raw
        WHERE source = ? AND raw_title = ? AND COALESCE(url, '') = COALESCE(?, '')
        LIMIT 1
        """,
        (trend["source"], trend["title"], trend["url"]),
    ).fetchone()
    return row is not None


def topic_exists(connection: sqlite3.Connection, trend: Trend) -> bool:
    """Return True if the hot topic already exists for the configured niche."""
    row = connection.execute(
        """
        SELECT id FROM topics_hot
        WHERE topic = ? AND COALESCE(niche, '') = COALESCE(?, '')
        LIMIT 1
        """,
        (trend["title"], NICHE),
    ).fetchone()
    return row is not None


def insert_raw_trends(connection: sqlite3.Connection, trends: list[Trend]) -> int:
    """Insert raw trends, skipping duplicates for idempotency."""
    inserted = 0
    collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for trend in trends:
        if trend_exists(connection, trend):
            continue
        connection.execute(
            """
            INSERT INTO trends_raw (source, raw_title, raw_text, url, collected_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (trend["source"], trend["title"], trend["text"], trend["url"], collected_at),
        )
        inserted += 1
    return inserted


def insert_hot_topics(connection: sqlite3.Connection, trends: list[Trend], limit: int = 5) -> int:
    """Insert top-scored trends into topics_hot with pending status."""
    inserted = 0
    for trend in sorted(trends, key=lambda item: item["score"], reverse=True)[:limit]:
        if topic_exists(connection, trend):
            continue
        connection.execute(
            """
            INSERT INTO topics_hot (topic, niche, viral_score, status)
            VALUES (?, ?, ?, ?)
            """,
            (trend["title"], NICHE, trend["score"], "pending"),
        )
        inserted += 1
    return inserted


def run_agent(payload: Any) -> dict[str, int]:
    """Run the Trendspotter ingestion flow and return a small execution summary."""
    trends = parse_trends(payload)
    with get_connection() as connection:
        raw_inserted = insert_raw_trends(connection, trends)
        topics_inserted = insert_hot_topics(connection, trends)
        connection.commit()

    summary = {
        "received": len(trends),
        "raw_inserted": raw_inserted,
        "topics_inserted": topics_inserted,
    }
    logging.info("Trendspotter completed: %s", summary)
    return summary


def main() -> int:
    """CLI entry point for n8n command execution or local testing."""
    parser = argparse.ArgumentParser(description="DualMind Trendspotter agent")
    parser.add_argument("input", nargs="?", help="Optional JSON payload file. Reads stdin when omitted.")
    args = parser.parse_args()

    configure_logging()
    try:
        payload = load_payload(args.input)
        summary = run_agent(payload)
    except (json.JSONDecodeError, OSError, ValueError, sqlite3.Error) as exc:
        logging.exception("Trendspotter failed")
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(json.dumps({"ok": True, **summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
