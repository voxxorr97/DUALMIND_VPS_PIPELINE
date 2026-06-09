"""Performance tracker agent for recently published DualMind YouTube videos.

The tracker reads YouTube videos published in the last seven days, refreshes
views/likes/comments through the YouTube Data API v3, updates SQLite, and sends
a WhatsApp alert when a video crosses the 10k-view viral threshold.

Usage examples::

    python scripts/agents/performance_tracker.py
    python scripts/agents/performance_tracker.py --limit 20
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path
from typing import Protocol, TypedDict

import requests
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "data" / "dualmind.db"
LOG_PATH = REPO_ROOT / "logs" / "performance_tracker.log"
DOTENV_PATH = REPO_ROOT / ".env"
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
REQUEST_TIMEOUT_SECONDS = 30
VIRAL_VIEW_THRESHOLD = 10_000
RECENT_DAYS = 7


class TrackedVideo(TypedDict):
    """Recently published YouTube video row selected for metric refresh."""

    performance_id: int
    script_id: int | None
    title: str
    video_id: str
    youtube_url: str
    views: int


class VideoMetrics(TypedDict):
    """YouTube statistics for one video."""

    views: int
    likes: int
    comments: int


class RunSummary(TypedDict):
    """Structured summary returned by the tracker run."""

    ok: bool
    videos_found: int
    metrics_updated: int
    viral_notifications: int
    skipped: int
    errors: int


class YouTubeMetricsClient(Protocol):
    """Minimal YouTube metrics client protocol."""

    def fetch_metrics(self, video_id: str) -> VideoMetrics:
        """Fetch current view/like/comment metrics."""


class WhatsAppNotifier(Protocol):
    """Minimal WhatsApp notification protocol."""

    def send_message(self, message: str) -> None:
        """Send one WhatsApp text message."""


def configure_logging() -> None:
    """Configure file logging for PerformanceTracker runs."""
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
    """Open the SQLite database and ensure tracker schema requirements exist."""
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
    """Create/migrate the SQLite tables used by the tracker."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS scripts_generated (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            script_text TEXT NOT NULL,
            platform TEXT NOT NULL,
            duration_seconds INTEGER,
            status TEXT NOT NULL DEFAULT 'draft',
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
    changed = ensure_column(connection, "scripts_generated", "youtube_url", "TEXT") or changed
    for column_name, definition in (
        ("script_id", "INTEGER"),
        ("video_id", "TEXT"),
        ("platform", "TEXT NOT NULL DEFAULT 'youtube'"),
        ("video_url", "TEXT"),
        ("youtube_url", "TEXT"),
        ("views", "INTEGER NOT NULL DEFAULT 0"),
        ("likes", "INTEGER NOT NULL DEFAULT 0"),
        ("comments", "INTEGER NOT NULL DEFAULT 0"),
        ("published_at", "TEXT"),
        ("analyzed_at", "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"),
    ):
        changed = ensure_column(connection, "video_performance", column_name, definition) or changed
    if changed:
        connection.commit()


def extract_video_id(youtube_url: str) -> str:
    """Extract a YouTube video id from a watch URL."""
    if "v=" in youtube_url:
        return youtube_url.split("v=", 1)[1].split("&", 1)[0]
    return youtube_url.rstrip("/").rsplit("/", 1)[-1]


def get_recent_videos(connection: sqlite3.Connection, limit: int = DEFAULT_LIMIT) -> list[TrackedVideo]:
    """Fetch YouTube performance rows published within the last seven days."""
    bounded_limit = min(max(limit, 0), MAX_LIMIT)
    rows = connection.execute(
        """
        SELECT
            vp.id AS performance_id,
            vp.script_id,
            COALESCE(sg.title, vp.video_id, vp.youtube_url, vp.video_url, 'Vidéo YouTube') AS title,
            COALESCE(vp.video_id, '') AS video_id,
            COALESCE(vp.youtube_url, vp.video_url, sg.youtube_url, '') AS youtube_url,
            COALESCE(vp.views, 0) AS views
        FROM video_performance vp
        LEFT JOIN scripts_generated sg ON sg.id = vp.script_id
        WHERE vp.platform = 'youtube'
          AND vp.published_at IS NOT NULL
          AND datetime(vp.published_at) >= datetime('now', ?)
        ORDER BY datetime(vp.published_at) DESC, vp.id ASC
        LIMIT ?
        """,
        (f"-{RECENT_DAYS} days", bounded_limit),
    ).fetchall()
    videos: list[TrackedVideo] = []
    for row in rows:
        youtube_url = str(row["youtube_url"] or "")
        video_id = str(row["video_id"] or "") or extract_video_id(youtube_url)
        if not video_id:
            continue
        videos.append(
            {
                "performance_id": int(row["performance_id"]),
                "script_id": int(row["script_id"]) if row["script_id"] is not None else None,
                "title": str(row["title"]),
                "video_id": video_id,
                "youtube_url": youtube_url,
                "views": int(row["views"] or 0),
            }
        )
    return videos


class RequestsYouTubeMetricsClient:
    """YouTube Data API v3 statistics client using refresh-token OAuth."""

    def __init__(self) -> None:
        self.client_id = require_env("YOUTUBE_CLIENT_ID")
        self.client_secret = require_env("YOUTUBE_CLIENT_SECRET")
        self.refresh_token = require_env("YOUTUBE_REFRESH_TOKEN")

    def refresh_access_token(self) -> str:
        """Exchange the configured refresh token for an access token."""
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
        access_token = response.json().get("access_token")
        if not access_token:
            raise RuntimeError(f"YouTube OAuth refresh did not return access_token: {response.text}")
        return str(access_token)

    def fetch_metrics(self, video_id: str) -> VideoMetrics:
        """Fetch current metrics for a YouTube video."""
        response = requests.get(
            YOUTUBE_VIDEOS_URL,
            params={"part": "statistics", "id": video_id},
            headers={"Authorization": f"Bearer {self.refresh_access_token()}"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code == 403 and "quotaExceeded" in response.text:
            logging.error("YouTube quotaExceeded while fetching metrics for video_id=%s: %s", video_id, response.text)
        if response.status_code >= 400:
            raise RuntimeError(f"YouTube metrics failed: {response.status_code} {response.text}")
        items = response.json().get("items", [])
        if not items:
            raise RuntimeError(f"YouTube metrics returned no item for video_id={video_id}")
        stats = items[0].get("statistics", {})
        return {
            "views": int(stats.get("viewCount", 0)),
            "likes": int(stats.get("likeCount", 0)),
            "comments": int(stats.get("commentCount", 0)),
        }


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


def update_metrics(connection: sqlite3.Connection, performance_id: int, metrics: VideoMetrics) -> None:
    """Persist refreshed metrics to one video_performance row."""
    with connection:
        connection.execute(
            """
            UPDATE video_performance
            SET views = ?, likes = ?, comments = ?, analyzed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (metrics["views"], metrics["likes"], metrics["comments"], performance_id),
        )


def run_agent(
    limit: int = DEFAULT_LIMIT,
    metrics_client: YouTubeMetricsClient | None = None,
    notifier: WhatsAppNotifier | None = None,
) -> RunSummary:
    """Run the PerformanceTracker agent once."""
    summary: RunSummary = {
        "ok": True,
        "videos_found": 0,
        "metrics_updated": 0,
        "viral_notifications": 0,
        "skipped": 0,
        "errors": 0,
    }
    real_client = metrics_client or RequestsYouTubeMetricsClient()
    real_notifier = notifier or WhatsAppBusinessNotifier()
    with get_connection() as connection:
        videos = get_recent_videos(connection, limit=limit)
        summary["videos_found"] = len(videos)
        for video in videos:
            try:
                metrics = real_client.fetch_metrics(video["video_id"])
                update_metrics(connection, video["performance_id"], metrics)
                summary["metrics_updated"] += 1
                if video["views"] < VIRAL_VIEW_THRESHOLD <= metrics["views"]:
                    real_notifier.send_message(f"🔥 VIRAL : {video['title']} — {metrics['views']} vues")
                    summary["viral_notifications"] += 1
                logging.info("Updated video_id=%s metrics=%s", video["video_id"], metrics)
            except Exception as exc:  # noqa: BLE001 - keep tracking following videos.
                logging.exception("Failed to update video_id=%s: %s", video["video_id"], exc)
                summary["errors"] += 1
                summary["skipped"] += 1
    summary["ok"] = summary["errors"] == 0
    return summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Refresh YouTube performance metrics for DualMind videos.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Maximum videos to track (max {MAX_LIMIT}).")
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
