"""Standalone smoke test for the DualMind Uploader agent.

The test uses no external network service: YouTube upload and WhatsApp delivery
are mocked in process, while SQLite writes are performed against a temporary DB.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

TEST_ROOT = Path(__file__).resolve().parents[2] / "tmp" / "uploader_test"
TEST_DB_PATH = TEST_ROOT / "dualmind.db"
TEST_VIDEO_PATH = TEST_ROOT / "videos" / "test_script.mp4"
os.environ["SQLITE_DB_PATH"] = str(TEST_DB_PATH)

import uploader


class MockYouTubeUploader:
    """Return a deterministic fake YouTube URL instead of uploading."""

    def upload_video(self, video_path: Path, title: str, description: str) -> uploader.PublishedVideo:
        print("\n[MOCK YOUTUBE UPLOAD]")
        print(json.dumps({"video_path": str(video_path), "title": title, "description": description}, ensure_ascii=False, indent=2))
        return {"video_id": "TEST123", "youtube_url": "https://www.youtube.com/watch?v=TEST123"}


class MockWhatsAppNotifier:
    """Print WhatsApp notifications instead of sending them."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, message: str) -> None:
        self.messages.append(message)
        print("\n[MOCK WHATSAPP]")
        print(message)


def reset_test_environment() -> int:
    """Create a fresh test DB with one assembled script and a fake MP4."""
    if TEST_ROOT.exists():
        shutil.rmtree(TEST_ROOT)
    TEST_VIDEO_PATH.parent.mkdir(parents=True, exist_ok=True)
    TEST_VIDEO_PATH.write_bytes(b"\x00\x00\x00\x18ftypmp42 mock mp4 payload")
    with uploader.get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO scripts_generated (
                topic_id, title, script_text, platform, duration_seconds, status, video_path
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                None,
                "Le dossier impossible de la route 17",
                """
[HOOK] (0-5s) : Cette disparition sur une route française n'a jamais eu d'explication.
[DÉVELOPPEMENT] (5-45s) : Trois témoins décrivent la même voiture sans plaque. Les caméras tombent en panne au même moment. Le dernier appel contient seulement un souffle et deux mots inaudibles.
[RÉVÉLATION] (45-55s) : Le véhicule est retrouvé vingt ans plus tôt dans une archive municipale.
[CTA] (55-60s) : Accident, mise en scène ou dossier vraiment impossible ?
""".strip(),
                "youtube_shorts_tiktok",
                60,
                "assembled",
                str(TEST_VIDEO_PATH),
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def fetch_script(script_id: int) -> dict[str, Any]:
    """Fetch the updated script row."""
    with uploader.get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, title, status, youtube_url, video_path
            FROM scripts_generated
            WHERE id = ?
            """,
            (script_id,),
        ).fetchone()
    return dict(row) if row is not None else {}


def fetch_performance(script_id: int) -> list[dict[str, Any]]:
    """Fetch performance rows for the test script."""
    with uploader.get_connection() as connection:
        rows = connection.execute(
            """
            SELECT script_id, video_id, platform, video_url, youtube_url, views, likes, comments, published_at
            FROM video_performance
            WHERE script_id = ?
            ORDER BY id ASC
            """,
            (script_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def assert_expected_results(summary: uploader.RunSummary, script: dict[str, Any], performance: list[dict[str, Any]]) -> None:
    """Validate SQLite updates from the mocked upload run."""
    if not summary["ok"]:
        raise AssertionError(f"Uploader summary is not ok: {summary}")
    if summary["assembled_found"] != 1 or summary["uploaded"] != 1 or summary["whatsapp_sent"] != 1:
        raise AssertionError(f"Unexpected summary counts: {summary}")
    if script.get("status") != "published":
        raise AssertionError(f"Expected published status, got: {script}")
    if script.get("youtube_url") != "https://www.youtube.com/watch?v=TEST123":
        raise AssertionError(f"youtube_url was not updated: {script}")
    if len(performance) != 1:
        raise AssertionError(f"Expected one video_performance row, got: {performance}")
    row = performance[0]
    if row.get("video_id") != "TEST123" or row.get("youtube_url") != "https://www.youtube.com/watch?v=TEST123":
        raise AssertionError(f"Performance URL/id mismatch: {row}")
    if row.get("views") != 0 or row.get("likes") != 0 or row.get("comments") != 0:
        raise AssertionError(f"Initial metrics should be zero: {row}")


def main() -> int:
    """Run the standalone Uploader test."""
    uploader.configure_logging()
    script_id = reset_test_environment()
    notifier = MockWhatsAppNotifier()
    summary = uploader.run_agent(limit=1, uploader=MockYouTubeUploader(), notifier=notifier)
    script = fetch_script(script_id)
    performance = fetch_performance(script_id)
    assert_expected_results(summary, script, performance)

    print(f"\nBase SQLite temporaire: {TEST_DB_PATH}")
    print("\nRésumé agent:")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("\nScript mis à jour:")
    print(json.dumps(script, ensure_ascii=False, indent=2))
    print("\nVideo performance:")
    print(json.dumps(performance, ensure_ascii=False, indent=2))
    print("\nNotifications WhatsApp mockées:")
    print(json.dumps(notifier.messages, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
