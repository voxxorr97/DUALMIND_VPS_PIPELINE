"""Command-line initializer for the Archive Noire SQLite memory core."""

from __future__ import annotations

from db import DB_PATH, init_db, insert_demo_data


def main() -> None:
    """Create the database, create tables, insert demo data, and report success."""
    init_db()
    insert_demo_data()
    print(f"SQLite memory core initialized successfully: {DB_PATH}")


if __name__ == "__main__":
    main()
