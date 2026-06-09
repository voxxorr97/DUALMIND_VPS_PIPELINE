"""SQLite helpers for the Archive Noire memory core.

The database is stored at ``data/dualmind.db`` relative to the repository root.
Only the Python standard library is used so this module stays portable on
Python 3.10+ environments.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[1]
DATA_DIR: Final[Path] = REPO_ROOT / "data"
DB_PATH: Final[Path] = DATA_DIR / "dualmind.db"


def get_connection() -> sqlite3.Connection:
    """Return a SQLite connection, creating the data directory if needed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def init_db() -> None:
    """Create the Archive Noire memory tables when they do not exist."""
    with get_connection() as connection:
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

            CREATE TABLE IF NOT EXISTS video_performance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                script_id INTEGER,
                platform TEXT NOT NULL,
                video_url TEXT,
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


def insert_demo_data() -> None:
    """Insert a small deterministic demo record set for smoke testing."""
    init_db()
    with get_connection() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO trends_raw (source, raw_title, raw_text, url)
            VALUES (?, ?, ?, ?)
            """,
            (
                "demo",
                "Signal faible Archive Noire",
                "Exemple local pour valider la mémoire SQLite du pipeline.",
                "https://example.com/archive-noire-demo",
            ),
        )
        cursor.execute(
            """
            INSERT INTO topics_hot (topic, niche, viral_score, status)
            VALUES (?, ?, ?, ?)
            """,
            ("Mémoire SQLite locale", "automation", 0.75, "demo"),
        )
        topic_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO scripts_generated (
                topic_id, title, script_text, platform, duration_seconds, status
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                topic_id,
                "Pourquoi une mémoire locale change tout",
                "Script de démonstration pour tester la table scripts_generated.",
                "shorts",
                45,
                "demo",
            ),
        )
        script_id = cursor.lastrowid
        cursor.execute(
            """
            INSERT INTO prompts_history (
                prompt_type, model, prompt_text, output_summary
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                "demo",
                "local-placeholder",
                "Génère un script court sur la mémoire SQLite locale.",
                "Donnée de test sans appel API ni secret.",
            ),
        )
        cursor.execute(
            """
            INSERT INTO video_performance (
                script_id, platform, video_url, views, likes, comments, watch_time_avg
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (script_id, "shorts", "https://example.com/demo-video", 0, 0, 0, 0.0),
        )
        connection.commit()
