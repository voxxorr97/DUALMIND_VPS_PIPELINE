"""Uploader agent for the DualMind v2.2 pipeline.

This sixth and final pipeline agent reads assembled videos from SQLite, uploads
MP4 files to YouTube with a non-interactive OAuth refresh token flow, sends a
WhatsApp Business notification, and records initial performance rows.

Usage examples::

    python scripts/agents/uploader.py
    python scripts/agents/uploader.py --limit 1
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
from pathlib import Path
from typing import Any, Protocol, TypedDict

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "dualmind.db"
LOG_PATH = REPO_ROOT / "logs" / "uploader.log"
DOTENV_PATH = REPO_ROOT / ".env"
DEFAULT_LIMIT = 3
MAX_LIMIT = 3
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
YOUTUBE_CATEGORY_ID = "22"
YOUTUBE_PRIVACY_STATUS = "public"
YOUTUBE_TAGS = [
    "mystère",
    "inexpliqué",
    "faits divers",
    "affaires non classées",
    "true crime france",
]
DEFAULT_HASHTAGS = ["#Mystère", "#Inexpliqué", "#AffairesNonClassées", "#TrueCrimeFrance"]
MAX_TITLE_LENGTH = 80
NETWORK_RETRIES = 2
REQUEST_TIMEOUT_SECONDS = 30
CHUNK_SIZE = 8 * 1024 * 1024


class AssembledScript(TypedDict):
    """Script row selected for YouTube publication."""

    id: int
    title: str
    script_text: str
    video_path: str | None


class PublishedVideo(TypedDict):
    """Publication metadata returned by an uploader implementation."""

    video_id: str
    youtube_url: str


class RunSummary(TypedDict):
    """Structured summary returned by the uploader run."""

    ok: bool
    assembled_found: int
    uploaded: int
    whatsapp_sent: int
    performance_rows: int
    skipped: int
    errors: int


class YouTubeUploader(Protocol):
    """Minimal YouTube uploader protocol used by the agent and tests."""

    def upload_video(self, video_path: Path, title: str, description: str) -> PublishedVideo:
        """Upload an MP4 and return its YouTube metadata."""


class WhatsAppNotifier(Protocol):
    """Minimal WhatsApp notification protocol used by the agent and tests."""

    def send_message(self, message: str) -> None:
        """Send one WhatsApp text message."""


def configure_logging() -> None:
    """Configure file logging for Uploader runs."""
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


def resolve_existing_path(raw_path: str | None) -> Path | None:
    """Resolve a DB path that may be absolute, repo-relative, or cwd-relative."""
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    candidates = [path] if path.is_absolute() else [REPO_ROOT / path, Path.cwd() / path, path]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def get_connection() -> sqlite3.Connection:
    """Open the SQLite database and ensure Uploader schema requirements exist."""
    db_path = resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    ensure_schema(connection)
    return connection


def ensure_column(connection: sqlite3.Connection, table_name: str, column_name: str, definition: str) -> bool:
    """Add a column to a SQLite table when it is absent."""
    columns = {str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")}
    if column_name in columns:
        return False
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
    return True


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create required tables and migrate older local databases in place."""
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
            images_path TEXT,
            video_path TEXT,
            youtube_url TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS video_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            script_id INTEGER,
            video_id TEXT,
            platform TEXT NOT NULL DEFAULT 'youtube',
            video_url TEXT,
            youtube_url TEXT,
            views INTEGER NOT NULL DEFAULT 0,
            likes INTEGER NOT NULL DEFAULT 0,
            comments INTEGER NOT NULL DEFAULT 0,
            watch_time_avg REAL,
            published_at TEXT,
            analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (script_id) REFERENCES scripts_generated(id) ON DELETE SET NULL
        );
        """
    )
    changed = False
    for column_name, definition in (
        ("audio_path", "TEXT"),
        ("images_path", "TEXT"),
        ("video_path", "TEXT"),
        ("youtube_url", "TEXT"),
    ):
        changed = ensure_column(connection, "scripts_generated", column_name, definition) or changed
    for column_name, definition in (
        ("script_id", "INTEGER"),
        ("video_id", "TEXT"),
        ("platform", "TEXT NOT NULL DEFAULT 'youtube'"),
        ("video_url", "TEXT"),
        ("youtube_url", "TEXT"),
        ("views", "INTEGER NOT NULL DEFAULT 0"),
        ("likes", "INTEGER NOT NULL DEFAULT 0"),
        ("comments", "INTEGER NOT NULL DEFAULT 0"),
        ("watch_time_avg", "REAL"),
        ("published_at", "TEXT"),
        ("analyzed_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    ):
        changed = ensure_column(connection, "video_performance", column_name, definition) or changed
    if changed:
        connection.commit()


def get_assembled_scripts(connection: sqlite3.Connection, limit: int = DEFAULT_LIMIT) -> list[AssembledScript]:
    """Fetch assembled scripts that have not already been published."""
    bounded_limit = min(max(limit, 0), MAX_LIMIT)
    rows = connection.execute(
        """
        SELECT id, title, script_text, video_path
        FROM scripts_generated
        WHERE status = 'assembled'
          AND (youtube_url IS NULL OR TRIM(youtube_url) = '')
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
            "video_path": row["video_path"],
        }
        for row in rows
    ]


def extract_segment(script_text: str, segment_name: str) -> str:
    """Extract a named bracketed segment from a generated French script."""
    labels = ["HOOK", "DÉVELOPPEMENT", "DEVELOPPEMENT", "RÉVÉLATION", "REVELATION", "CTA"]
    label_pattern = "|".join(re.escape(label) for label in labels)
    pattern = re.compile(
        rf"\[{re.escape(segment_name)}\]\s*(?:\([^)]*\))?\s*:?\s*(.*?)(?=\n\s*\[(?:{label_pattern})\]|\Z)",
        flags=re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(script_text)
    if not match and segment_name == "DÉVELOPPEMENT":
        return extract_segment(script_text, "DEVELOPPEMENT")
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def clean_title(raw_title: str, fallback: str) -> str:
    """Reformat the HOOK segment into a YouTube-safe short title."""
    title = raw_title or fallback
    title = re.sub(r"^[\s:—–-]+", "", title)
    title = title.strip(" \t\n\r\"'«»")
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        title = fallback.strip() or "Affaire mystérieuse non classée"
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    truncated = title[: MAX_TITLE_LENGTH - 1].rstrip()
    if " " in truncated:
        truncated = truncated.rsplit(" ", 1)[0]
    return f"{truncated}…"


def build_youtube_title(script: AssembledScript) -> str:
    """Build the YouTube title from the script HOOK segment."""
    return clean_title(extract_segment(script["script_text"], "HOOK"), script["title"])


def build_youtube_description(script: AssembledScript) -> str:
    """Build the YouTube description from DÉVELOPPEMENT and automatic hashtags."""
    development = extract_segment(script["script_text"], "DÉVELOPPEMENT")
    if not development:
        development = script["script_text"][:900].strip()
    hashtags = " ".join(DEFAULT_HASHTAGS)
    return f"{development}\n\n{hashtags}".strip()


def extract_video_id(youtube_url: str) -> str:
    """Extract a YouTube video id from a watch URL, or return the raw URL tail."""
    match = re.search(r"[?&]v=([^&]+)", youtube_url)
    if match:
        return match.group(1)
    return youtube_url.rstrip("/").rsplit("/", 1)[-1]


class RequestsYouTubeUploader:
    """YouTube Data API v3 resumable uploader using OAuth refresh-token auth."""

    def __init__(self) -> None:
        self.client_id = require_env("YOUTUBE_CLIENT_ID")
        self.client_secret = require_env("YOUTUBE_CLIENT_SECRET")
        self.refresh_token = require_env("YOUTUBE_REFRESH_TOKEN")

    def refresh_access_token(self) -> str:
        """Exchange the configured refresh token for a short-lived access token."""
        response = requests.post(
            YOUTUBE_TOKEN_URL,
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"YouTube OAuth refresh failed: {response.status_code} {response.text}")
        data = response.json()
        access_token = data.get("access_token")
        if not access_token:
            raise RuntimeError(f"YouTube OAuth refresh did not return access_token: {data}")
        return str(access_token)

    def upload_video(self, video_path: Path, title: str, description: str) -> PublishedVideo:
        """Upload an MP4 with YouTube resumable upload and return URL metadata."""
        access_token = self.refresh_access_token()
        metadata = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": YOUTUBE_TAGS,
                "categoryId": os.environ.get("YOUTUBE_CATEGORY_ID", YOUTUBE_CATEGORY_ID),
            },
            "status": {
                "privacyStatus": os.environ.get("YOUTUBE_UPLOAD_PRIVACY_STATUS", YOUTUBE_PRIVACY_STATUS),
                "selfDeclaredMadeForKids": False,
            },
        }
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(video_path.stat().st_size),
        }
        session_response = requests.post(
            YOUTUBE_UPLOAD_URL,
            params={"part": "snippet,status", "uploadType": "resumable"},
            headers=headers,
            data=json.dumps(metadata, ensure_ascii=False).encode("utf-8"),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if session_response.status_code == 403 and "quotaExceeded" in session_response.text:
            logging.error("YouTube quotaExceeded while creating resumable upload session: %s", session_response.text)
        if session_response.status_code >= 400:
            raise RuntimeError(
                f"YouTube resumable session failed: {session_response.status_code} {session_response.text}"
            )
        upload_url = session_response.headers.get("Location")
        if not upload_url:
            raise RuntimeError("YouTube resumable session did not return a Location header.")

        with video_path.open("rb") as video_file:
            upload_response = requests.put(
                upload_url,
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "video/mp4"},
                data=video_file,
                timeout=None,
            )
        if upload_response.status_code == 403 and "quotaExceeded" in upload_response.text:
            logging.error("YouTube quotaExceeded during resumable upload: %s", upload_response.text)
        if upload_response.status_code >= 400:
            raise RuntimeError(f"YouTube upload failed: {upload_response.status_code} {upload_response.text}")
        data = upload_response.json()
        video_id = str(data.get("id") or "").strip()
        if not video_id:
            raise RuntimeError(f"YouTube upload response did not include video id: {data}")
        return {"video_id": video_id, "youtube_url": YOUTUBE_WATCH_URL.format(video_id=video_id)}


class WhatsAppBusinessNotifier:
    """WhatsApp Business Cloud/API notifier."""

    def __init__(self) -> None:
        self.api_url = os.environ.get("WHATSAPP_API_URL", "").strip()
        self.phone_number_id = os.environ.get("WHATSAPP_PHONE_NUMBER_ID") or os.environ.get(
            "WHATSAPP_BUSINESS_PHONE_NUMBER_ID", ""
        )
        self.access_token = require_env("WHATSAPP_ACCESS_TOKEN")
        self.recipient_number = require_env("WHATSAPP_RECIPIENT_NUMBER")

    def endpoint(self) -> str:
        """Return the configured WhatsApp messages endpoint."""
        if self.api_url:
            return self.api_url
        if not self.phone_number_id:
            raise RuntimeError("WHATSAPP_API_URL or WHATSAPP_PHONE_NUMBER_ID is required.")
        return f"https://graph.facebook.com/v20.0/{self.phone_number_id}/messages"

    def send_message(self, message: str) -> None:
        """Send one WhatsApp Business text message."""
        response = requests.post(
            self.endpoint(),
            headers={"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "to": self.recipient_number,
                "type": "text",
                "text": {"preview_url": True, "body": message},
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"WhatsApp notification failed: {response.status_code} {response.text}")


def require_env(name: str) -> str:
    """Return a required environment variable or raise a clear error."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required in .env or the process environment.")
    return value


def upload_with_retry(uploader: YouTubeUploader, video_path: Path, title: str, description: str) -> PublishedVideo:
    """Upload with two retries for network/API errors before skipping the video."""
    last_error: Exception | None = None
    for attempt in range(NETWORK_RETRIES + 1):
        try:
            return uploader.upload_video(video_path, title, description)
        except Exception as exc:  # noqa: BLE001 - HTTP clients raise heterogeneous exceptions.
            last_error = exc
            message = str(exc)
            if "quotaExceeded" in message:
                logging.error("YouTube quotaExceeded; skipping upload cleanly: %s", message)
                break
            logging.warning("YouTube upload failed on attempt %s/%s: %s", attempt + 1, NETWORK_RETRIES + 1, exc)
            if attempt < NETWORK_RETRIES:
                time.sleep(2**attempt)
    raise RuntimeError(f"YouTube upload failed after retries: {last_error}")


def mark_published(
    connection: sqlite3.Connection,
    script_id: int,
    video_id: str,
    youtube_url: str,
) -> None:
    """Update scripts_generated and insert one initial video_performance row."""
    with connection:
        connection.execute(
            """
            UPDATE scripts_generated
            SET status = 'published', youtube_url = ?
            WHERE id = ? AND status = 'assembled'
            """,
            (youtube_url, script_id),
        )
        existing = connection.execute(
            """
            SELECT 1
            FROM video_performance
            WHERE script_id = ? AND platform = 'youtube'
            LIMIT 1
            """,
            (script_id,),
        ).fetchone()
        if existing is None:
            connection.execute(
                """
                INSERT INTO video_performance (
                    script_id, video_id, platform, video_url, youtube_url,
                    published_at, views, likes, comments
                )
                VALUES (?, ?, 'youtube', ?, ?, CURRENT_TIMESTAMP, 0, 0, 0)
                """,
                (script_id, video_id, youtube_url, youtube_url),
            )


def run_agent(
    limit: int = DEFAULT_LIMIT,
    uploader: YouTubeUploader | None = None,
    notifier: WhatsAppNotifier | None = None,
) -> RunSummary:
    """Run the Uploader agent once."""
    summary: RunSummary = {
        "ok": True,
        "assembled_found": 0,
        "uploaded": 0,
        "whatsapp_sent": 0,
        "performance_rows": 0,
        "skipped": 0,
        "errors": 0,
    }
    real_uploader = uploader or RequestsYouTubeUploader()
    real_notifier = notifier or WhatsAppBusinessNotifier()

    with get_connection() as connection:
        scripts = get_assembled_scripts(connection, limit=limit)
        summary["assembled_found"] = len(scripts)
        for script in scripts:
            video_path = resolve_existing_path(script["video_path"])
            if video_path is None or not video_path.exists():
                logging.error("Skipping script_id=%s because video_path is missing: %s", script["id"], script["video_path"])
                summary["skipped"] += 1
                continue
            title = build_youtube_title(script)
            description = build_youtube_description(script)
            try:
                published = upload_with_retry(real_uploader, video_path, title, description)
                youtube_url = published.get("youtube_url") or YOUTUBE_WATCH_URL.format(video_id=published["video_id"])
                video_id = published.get("video_id") or extract_video_id(youtube_url)
                mark_published(connection, script["id"], video_id, youtube_url)
                summary["uploaded"] += 1
                summary["performance_rows"] += 1
                message = f"✅ Vidéo publiée : {title} | {youtube_url} | Views cible : 10k en 48h"
                real_notifier.send_message(message)
                summary["whatsapp_sent"] += 1
                logging.info("Published script_id=%s video_id=%s url=%s", script["id"], video_id, youtube_url)
            except Exception as exc:  # noqa: BLE001 - keep processing following assembled videos.
                logging.exception("Failed to publish script_id=%s: %s", script["id"], exc)
                summary["errors"] += 1
                summary["skipped"] += 1
    summary["ok"] = summary["errors"] == 0
    return summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Upload assembled DualMind videos to YouTube.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Maximum assembled scripts to publish (max {MAX_LIMIT}).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    configure_logging()
    load_environment()
    args = parse_args(argv if argv is not None else sys.argv[1:])
    summary = run_agent(limit=args.limit)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
