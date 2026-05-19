from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

import psycopg

from telegram_exam_bot.database import Database


TABLES = (
    "users",
    "user_settings",
    "bot_settings",
    "tests",
    "questions",
    "answers",
    "attempts",
    "attempt_answers",
    "test_shares",
)


def main() -> None:
    sqlite_path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/exam_bot.sqlite3")
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL before running migration.")
    if not sqlite_path.exists():
        raise RuntimeError(f"SQLite database not found: {sqlite_path}")

    Database(database_url).initialize()

    source = sqlite3.connect(sqlite_path)
    source.row_factory = sqlite3.Row
    target = psycopg.connect(database_url)
    try:
        with target.transaction():
            for table in TABLES:
                rows = source.execute(f"SELECT * FROM {table}").fetchall()
                for row in rows:
                    insert_row(target, table, dict(row))
            reset_sequence(target, "tests")
            reset_sequence(target, "questions")
            reset_sequence(target, "answers")
            reset_sequence(target, "attempts")
            reset_sequence(target, "attempt_answers")
    finally:
        source.close()
        target.close()

    print("Migration completed.")


def insert_row(connection: Any, table: str, row: dict[str, Any]) -> None:
    columns = list(row)
    placeholders = ", ".join("%s" for _ in columns)
    names = ", ".join(columns)
    conflict = conflict_clause(table)
    sql = f"INSERT INTO {table} ({names}) VALUES ({placeholders}) {conflict}"
    connection.execute(sql, tuple(row[column] for column in columns))


def conflict_clause(table: str) -> str:
    if table in {"users", "user_settings"}:
        return "ON CONFLICT (user_id) DO NOTHING"
    if table == "bot_settings":
        return "ON CONFLICT (key) DO NOTHING"
    if table == "test_shares":
        return "ON CONFLICT (token) DO NOTHING"
    return "ON CONFLICT (id) DO NOTHING"


def reset_sequence(connection: Any, table: str) -> None:
    connection.execute(
        f"""
        SELECT setval(
            pg_get_serial_sequence('{table}', 'id'),
            GREATEST(COALESCE((SELECT max(id) FROM {table}), 1), 1),
            true
        )
        """
    )


if __name__ == "__main__":
    main()
