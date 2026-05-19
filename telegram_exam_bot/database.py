from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import sqlite3
from typing import Any

from .parser import ParsedTest


@dataclass(frozen=True)
class DbAnswer:
    id: int
    text: str
    is_correct: bool


@dataclass(frozen=True)
class DbQuestion:
    id: int
    text: str
    answers: tuple[DbAnswer, ...]


@dataclass(frozen=True)
class DbTest:
    id: int
    owner_id: int
    title: str
    source_filename: str
    question_count: int
    created_at: str
    source_test_id: int | None
    questions: tuple[DbQuestion, ...]


@dataclass(frozen=True)
class AttemptChoice:
    question_id: int
    selected_answer_id: int
    correct_answer_id: int
    is_correct: bool


class Database:
    def __init__(self, path: str | Path) -> None:
        self.location = str(path)
        self.is_postgres = self.location.startswith(("postgres://", "postgresql://"))
        self.path = None if self.is_postgres else Path(path)
        self._postgres_pool: Any | None = None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def initialize(self) -> None:
        if self.is_postgres:
            self._initialize_postgres()
            return

        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA foreign_keys = ON;

                CREATE TABLE IF NOT EXISTS tests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    source_filename TEXT NOT NULL,
                    question_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    source_test_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS questions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    position INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    is_correct INTEGER NOT NULL,
                    position INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    correct_count INTEGER NOT NULL,
                    total_count INTEGER NOT NULL,
                    percent REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS attempt_answers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id INTEGER NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
                    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    selected_answer_id INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
                    correct_answer_id INTEGER NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
                    is_correct INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    language TEXT NOT NULL DEFAULT 'ru',
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    last_seen_at TEXT NOT NULL,
                    is_blocked INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS test_shares (
                    token TEXT PRIMARY KEY,
                    test_id INTEGER NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_tests_owner ON tests(owner_id);
                CREATE INDEX IF NOT EXISTS idx_attempts_user ON attempts(user_id, finished_at);
                CREATE INDEX IF NOT EXISTS idx_users_seen ON users(last_seen_at);
                CREATE INDEX IF NOT EXISTS idx_test_shares_test ON test_shares(test_id);
                """
            )
            self._ensure_column(connection, "tests", "source_test_id", "INTEGER")
            self._ensure_column(connection, "users", "is_blocked", "INTEGER NOT NULL DEFAULT 0")
            now = _utc_now()
            connection.execute(
                """
                INSERT OR IGNORE INTO users (user_id, last_seen_at)
                SELECT DISTINCT owner_id, ? FROM tests
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO users (user_id, last_seen_at)
                SELECT DISTINCT user_id, ? FROM attempts
                """,
                (now,),
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_tests_source ON tests(source_test_id)"
            )
            self._repair_rtf_fallback_artifacts(connection)

    def _initialize_postgres(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tests (
                    id BIGSERIAL PRIMARY KEY,
                    owner_id BIGINT NOT NULL,
                    title TEXT NOT NULL,
                    source_filename TEXT NOT NULL,
                    question_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    source_test_id BIGINT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS questions (
                    id BIGSERIAL PRIMARY KEY,
                    test_id BIGINT NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    position INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS answers (
                    id BIGSERIAL PRIMARY KEY,
                    question_id BIGINT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    text TEXT NOT NULL,
                    is_correct INTEGER NOT NULL,
                    position INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attempts (
                    id BIGSERIAL PRIMARY KEY,
                    test_id BIGINT NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    correct_count INTEGER NOT NULL,
                    total_count INTEGER NOT NULL,
                    percent REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attempt_answers (
                    id BIGSERIAL PRIMARY KEY,
                    attempt_id BIGINT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
                    question_id BIGINT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
                    selected_answer_id BIGINT NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
                    correct_answer_id BIGINT NOT NULL REFERENCES answers(id) ON DELETE CASCADE,
                    is_correct INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id BIGINT PRIMARY KEY,
                    language TEXT NOT NULL DEFAULT 'ru',
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    last_seen_at TEXT NOT NULL,
                    is_blocked INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS test_shares (
                    token TEXT PRIMARY KEY,
                    test_id BIGINT NOT NULL REFERENCES tests(id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_tests_owner ON tests(owner_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_attempts_user ON attempts(user_id, finished_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_users_seen ON users(last_seen_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_test_shares_test ON test_shares(test_id)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_tests_source ON tests(source_test_id)")

            now = _utc_now()
            connection.execute(
                """
                INSERT INTO users (user_id, last_seen_at)
                SELECT DISTINCT owner_id, ? FROM tests
                ON CONFLICT (user_id) DO NOTHING
                """,
                (now,),
            )
            connection.execute(
                """
                INSERT INTO users (user_id, last_seen_at)
                SELECT DISTINCT user_id, ? FROM attempts
                ON CONFLICT (user_id) DO NOTHING
                """,
                (now,),
            )
            self._repair_rtf_fallback_artifacts(connection)

    def upsert_user(
        self,
        user_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO users (user_id, username, first_name, last_name, last_seen_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    last_seen_at = excluded.last_seen_at
                """,
                (user_id, username, first_name, last_name, _utc_now()),
            )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT value FROM bot_settings WHERE key = ?",
                (key,),
            ).fetchone()
            return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO bot_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, _utc_now()),
            )

    def admin_stats(self) -> dict[str, int]:
        with self._connection() as connection:
            return {
                "users": int(connection.execute("SELECT count(*) FROM users").fetchone()[0]),
                "blocked_users": int(
                    connection.execute("SELECT count(*) FROM users WHERE is_blocked = 1").fetchone()[0]
                ),
                "tests": int(connection.execute("SELECT count(*) FROM tests").fetchone()[0]),
                "attempts": int(connection.execute("SELECT count(*) FROM attempts").fetchone()[0]),
                "active_shares": int(connection.execute("SELECT count(*) FROM test_shares").fetchone()[0]),
            }

    def is_user_blocked(self, user_id: int) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT is_blocked FROM users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return bool(row["is_blocked"]) if row else False

    def set_user_blocked(self, user_id: int, blocked: bool) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO users (user_id, last_seen_at, is_blocked)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    is_blocked = excluded.is_blocked
                """,
                (user_id, _utc_now(), 1 if blocked else 0),
            )

    def admin_list_users(self, limit: int = 25) -> list[sqlite3.Row]:
        with self._connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        users.user_id,
                        users.username,
                        users.first_name,
                        users.last_name,
                        users.last_seen_at,
                        users.is_blocked,
                        (SELECT count(*) FROM tests WHERE tests.owner_id = users.user_id) AS test_count,
                        (SELECT count(*) FROM attempts WHERE attempts.user_id = users.user_id) AS attempt_count,
                        COALESCE(
                            (SELECT round(avg(percent), 2) FROM attempts WHERE attempts.user_id = users.user_id),
                            0
                        ) AS avg_percent
                    FROM users
                    ORDER BY users.last_seen_at DESC, users.user_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def admin_get_user(self, user_id: int) -> sqlite3.Row | None:
        with self._connection() as connection:
            return connection.execute(
                """
                SELECT
                    users.user_id,
                    users.username,
                    users.first_name,
                    users.last_name,
                    users.last_seen_at,
                    users.is_blocked,
                    (SELECT count(*) FROM tests WHERE tests.owner_id = users.user_id) AS test_count,
                    (SELECT count(*) FROM attempts WHERE attempts.user_id = users.user_id) AS attempt_count,
                    COALESCE(
                        (SELECT round(avg(percent), 2) FROM attempts WHERE attempts.user_id = users.user_id),
                        0
                    ) AS avg_percent
                FROM users
                WHERE users.user_id = ?
                """,
                (user_id,),
            ).fetchone()

    def admin_user_tests(self, user_id: int, limit: int = 25) -> list[sqlite3.Row]:
        with self._connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        tests.id,
                        tests.title,
                        tests.source_filename,
                        tests.question_count,
                        tests.created_at,
                        tests.source_test_id,
                        (SELECT count(*) FROM attempts WHERE attempts.test_id = tests.id) AS attempt_count,
                        COALESCE(
                            (SELECT round(avg(percent), 2) FROM attempts WHERE attempts.test_id = tests.id),
                            0
                        ) AS avg_percent
                    FROM tests
                    WHERE tests.owner_id = ?
                    ORDER BY tests.created_at DESC, tests.id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                )
            )

    def admin_recent_tests(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        tests.id,
                        tests.title,
                        tests.source_filename,
                        tests.question_count,
                        tests.created_at,
                        tests.source_test_id,
                        COALESCE(users.user_id, tests.owner_id) AS user_id,
                        users.username,
                        users.first_name,
                        users.last_name
                    FROM tests
                    LEFT JOIN users ON users.user_id = tests.owner_id
                    ORDER BY tests.created_at DESC, tests.id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def admin_recent_attempts(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        attempts.id,
                        attempts.correct_count,
                        attempts.total_count,
                        attempts.percent,
                        attempts.finished_at,
                        tests.title,
                        COALESCE(users.user_id, attempts.user_id) AS user_id,
                        users.username,
                        users.first_name,
                        users.last_name
                    FROM attempts
                    JOIN tests ON tests.id = attempts.test_id
                    LEFT JOIN users ON users.user_id = attempts.user_id
                    ORDER BY attempts.finished_at DESC, attempts.id DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            )

    def get_user_language(self, user_id: int) -> str:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT language FROM user_settings WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            return str(row["language"]) if row else "ru"

    def set_user_language(self, user_id: int, language: str) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO user_settings (user_id, language, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    language = excluded.language,
                    updated_at = excluded.updated_at
                """,
                (user_id, language, _utc_now()),
            )

    def create_test(
        self,
        owner_id: int,
        source_filename: str,
        parsed_test: ParsedTest,
    ) -> int:
        now = _utc_now()
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO tests (owner_id, title, source_filename, question_count, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    owner_id,
                    parsed_test.title,
                    source_filename,
                    len(parsed_test.questions),
                    now,
                ),
            )
            test_id = int(cursor.lastrowid)

            if self.is_postgres and parsed_test.questions:
                question_placeholders = ",".join(
                    "(?, ?, ?)" for _ in parsed_test.questions
                )
                question_params: list[Any] = []
                for question_position, question in enumerate(parsed_test.questions, start=1):
                    question_params.extend((test_id, question.text, question_position))

                question_cursor = connection.execute(
                    f"""
                    INSERT INTO questions (test_id, text, position)
                    VALUES {question_placeholders}
                    RETURNING id
                    """,
                    question_params,
                )
                question_ids = [int(row["id"]) for row in question_cursor.fetchall()]

                answer_rows: list[tuple[int, str, int, int]] = []
                for question_id, question in zip(question_ids, parsed_test.questions):
                    for answer_position, answer in enumerate(question.answers, start=1):
                        answer_rows.append(
                            (
                                question_id,
                                answer,
                                1 if answer_position - 1 == question.correct_answer_index else 0,
                                answer_position,
                            )
                        )
                if answer_rows:
                    answer_placeholders = ",".join("(?, ?, ?, ?)" for _ in answer_rows)
                    answer_params = [
                        value
                        for answer_row in answer_rows
                        for value in answer_row
                    ]
                    connection.execute(
                        f"""
                        INSERT INTO answers (question_id, text, is_correct, position)
                        VALUES {answer_placeholders}
                        """,
                        answer_params,
                    )
            else:
                for question_position, question in enumerate(parsed_test.questions, start=1):
                    question_cursor = connection.execute(
                        """
                        INSERT INTO questions (test_id, text, position)
                        VALUES (?, ?, ?)
                        """,
                        (test_id, question.text, question_position),
                    )
                    question_id = int(question_cursor.lastrowid)

                    for answer_position, answer in enumerate(question.answers, start=1):
                        connection.execute(
                            """
                            INSERT INTO answers (question_id, text, is_correct, position)
                            VALUES (?, ?, ?, ?)
                            """,
                            (
                                question_id,
                                answer,
                                1 if answer_position - 1 == question.correct_answer_index else 0,
                                answer_position,
                            ),
                        )

        return test_id

    def list_tests(self, owner_id: int) -> list[sqlite3.Row]:
        with self._connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT id, title, source_filename, question_count, created_at, source_test_id
                    FROM tests
                    WHERE owner_id = ?
                    ORDER BY created_at DESC, id DESC
                    """,
                    (owner_id,),
                )
            )

    def get_test_summary(self, test_id: int, owner_id: int) -> sqlite3.Row | None:
        with self._connection() as connection:
            return connection.execute(
                """
                SELECT id, title, source_filename, question_count, created_at, source_test_id
                FROM tests
                WHERE id = ? AND owner_id = ?
                """,
                (test_id, owner_id),
            ).fetchone()

    def get_test_for_attempt(self, test_id: int, owner_id: int) -> DbTest | None:
        with self._connection() as connection:
            test_row = connection.execute(
                """
                SELECT id, owner_id, title, source_filename, question_count, created_at, source_test_id
                FROM tests
                WHERE id = ? AND owner_id = ?
                """,
                (test_id, owner_id),
            ).fetchone()
            if test_row is None:
                return None

            return DbTest(
                id=int(test_row["id"]),
                owner_id=int(test_row["owner_id"]),
                title=str(test_row["title"]),
                source_filename=str(test_row["source_filename"]),
                question_count=int(test_row["question_count"]),
                created_at=str(test_row["created_at"]),
                source_test_id=(
                    int(test_row["source_test_id"])
                    if test_row["source_test_id"] is not None
                    else None
                ),
                questions=self._load_questions(connection, test_id),
            )

    def admin_get_test(self, test_id: int) -> DbTest | None:
        with self._connection() as connection:
            test_row = connection.execute(
                """
                SELECT id, owner_id, title, source_filename, question_count, created_at, source_test_id
                FROM tests
                WHERE id = ?
                """,
                (test_id,),
            ).fetchone()
            if test_row is None:
                return None

            return DbTest(
                id=int(test_row["id"]),
                owner_id=int(test_row["owner_id"]),
                title=str(test_row["title"]),
                source_filename=str(test_row["source_filename"]),
                question_count=int(test_row["question_count"]),
                created_at=str(test_row["created_at"]),
                source_test_id=(
                    int(test_row["source_test_id"])
                    if test_row["source_test_id"] is not None
                    else None
                ),
                questions=self._load_questions(connection, test_id),
            )

    def delete_test(self, test_id: int, owner_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                "DELETE FROM tests WHERE id = ? AND owner_id = ?",
                (test_id, owner_id),
            )
            return cursor.rowcount > 0

    def delete_test_for_all(self, test_id: int, owner_id: int) -> list[int]:
        with self._connection() as connection:
            test = connection.execute(
                """
                SELECT id, source_test_id
                FROM tests
                WHERE id = ? AND owner_id = ?
                """,
                (test_id, owner_id),
            ).fetchone()
            if test is None or test["source_test_id"] is not None:
                return []

            rows = connection.execute(
                "SELECT id FROM tests WHERE id = ? OR source_test_id = ?",
                (test_id, test_id),
            ).fetchall()
            deleted_ids = [int(row["id"]) for row in rows]
            if deleted_ids:
                placeholders = ",".join("?" for _ in deleted_ids)
                connection.execute(
                    f"DELETE FROM tests WHERE id IN ({placeholders})",
                    deleted_ids,
                )
            return deleted_ids

    def admin_delete_test(self, test_id: int) -> bool:
        with self._connection() as connection:
            cursor = connection.execute("DELETE FROM tests WHERE id = ?", (test_id,))
            return cursor.rowcount > 0

    def admin_delete_test_for_all(self, test_id: int) -> list[int]:
        with self._connection() as connection:
            test = connection.execute(
                "SELECT id, source_test_id FROM tests WHERE id = ?",
                (test_id,),
            ).fetchone()
            if test is None:
                return []

            root_id = (
                int(test["source_test_id"])
                if test["source_test_id"] is not None
                else int(test["id"])
            )
            rows = connection.execute(
                "SELECT id FROM tests WHERE id = ? OR source_test_id = ?",
                (root_id, root_id),
            ).fetchall()
            deleted_ids = [int(row["id"]) for row in rows]
            if deleted_ids:
                placeholders = ",".join("?" for _ in deleted_ids)
                connection.execute(
                    f"DELETE FROM tests WHERE id IN ({placeholders})",
                    deleted_ids,
                )
            return deleted_ids

    def count_linked_copies(self, test_id: int) -> int:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT count(*) AS count FROM tests WHERE source_test_id = ?",
                (test_id,),
            ).fetchone()
            return int(row["count"]) if row else 0

    def get_or_create_share_token(self, test_id: int, owner_id: int) -> str | None:
        with self._connection() as connection:
            test = connection.execute(
                "SELECT id FROM tests WHERE id = ? AND owner_id = ?",
                (test_id, owner_id),
            ).fetchone()
            if test is None:
                return None

            existing = connection.execute(
                "SELECT token FROM test_shares WHERE test_id = ?",
                (test_id,),
            ).fetchone()
            if existing:
                return str(existing["token"])

            for _ in range(8):
                token = secrets.token_urlsafe(12)
                try:
                    connection.execute(
                        "INSERT INTO test_shares (token, test_id, created_at) VALUES (?, ?, ?)",
                        (token, test_id, _utc_now()),
                    )
                    return token
                except Exception as exc:
                    if not _is_integrity_error(exc):
                        raise
                    continue

        return None

    def get_shared_test_summary(self, token: str) -> sqlite3.Row | None:
        with self._connection() as connection:
            return connection.execute(
                """
                SELECT
                    tests.id,
                    tests.owner_id,
                    tests.title,
                    tests.source_filename,
                    tests.question_count,
                    tests.created_at,
                    tests.source_test_id
                FROM test_shares
                JOIN tests ON tests.id = test_shares.test_id
                WHERE test_shares.token = ?
                """,
                (token,),
            ).fetchone()

    def copy_shared_test_to_owner(self, token: str, owner_id: int) -> int | None:
        with self._connection() as connection:
            source = connection.execute(
                """
                SELECT tests.*
                FROM test_shares
                JOIN tests ON tests.id = test_shares.test_id
                WHERE test_shares.token = ?
                """,
                (token,),
            ).fetchone()
            if source is None:
                return None
            if int(source["owner_id"]) == owner_id:
                return int(source["id"])

            source_id = int(source["id"])
            root_source_id = (
                int(source["source_test_id"])
                if source["source_test_id"] is not None
                else source_id
            )
            now = _utc_now()
            cursor = connection.execute(
                """
                INSERT INTO tests (
                    owner_id, title, source_filename, question_count, created_at, source_test_id
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    owner_id,
                    str(source["title"]),
                    str(source["source_filename"]),
                    int(source["question_count"]),
                    now,
                    root_source_id,
                ),
            )
            new_test_id = int(cursor.lastrowid)

            question_rows = connection.execute(
                """
                SELECT id, text, position
                FROM questions
                WHERE test_id = ?
                ORDER BY position ASC
                """,
                (source_id,),
            ).fetchall()

            for question_row in question_rows:
                question_cursor = connection.execute(
                    """
                    INSERT INTO questions (test_id, text, position)
                    VALUES (?, ?, ?)
                    """,
                    (new_test_id, str(question_row["text"]), int(question_row["position"])),
                )
                new_question_id = int(question_cursor.lastrowid)
                answer_rows = connection.execute(
                    """
                    SELECT text, is_correct, position
                    FROM answers
                    WHERE question_id = ?
                    ORDER BY position ASC
                    """,
                    (int(question_row["id"]),),
                ).fetchall()
                for answer_row in answer_rows:
                    connection.execute(
                        """
                        INSERT INTO answers (question_id, text, is_correct, position)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            new_question_id,
                            str(answer_row["text"]),
                            int(answer_row["is_correct"]),
                            int(answer_row["position"]),
                        ),
                    )

            return new_test_id

    def get_mistake_questions_for_test(self, test_id: int, user_id: int) -> list[DbQuestion]:
        with self._connection() as connection:
            question_rows = connection.execute(
                """
                SELECT DISTINCT questions.id, questions.text
                FROM attempt_answers
                JOIN attempts ON attempts.id = attempt_answers.attempt_id
                JOIN questions ON questions.id = attempt_answers.question_id
                WHERE attempts.test_id = ?
                  AND attempts.user_id = ?
                  AND attempt_answers.is_correct = 0
                ORDER BY questions.id ASC
                """,
                (test_id, user_id),
            ).fetchall()

            question_ids = [int(question_row["id"]) for question_row in question_rows]
            answers_by_question = self._load_answers_for_questions(connection, question_ids)
            return [
                DbQuestion(
                    id=int(question_row["id"]),
                    text=str(question_row["text"]),
                    answers=tuple(answers_by_question.get(int(question_row["id"]), [])),
                )
                for question_row in question_rows
            ]

    def _load_questions(self, connection: Any, test_id: int) -> tuple[DbQuestion, ...]:
        question_rows = connection.execute(
            """
            SELECT id, text
            FROM questions
            WHERE test_id = ?
            ORDER BY position ASC
            """,
            (test_id,),
        ).fetchall()
        question_ids = [int(question_row["id"]) for question_row in question_rows]
        answers_by_question = self._load_answers_for_questions(connection, question_ids)
        return tuple(
            DbQuestion(
                id=int(question_row["id"]),
                text=str(question_row["text"]),
                answers=tuple(answers_by_question.get(int(question_row["id"]), [])),
            )
            for question_row in question_rows
        )

    def _load_answers_for_questions(
        self,
        connection: Any,
        question_ids: list[int],
    ) -> dict[int, list[DbAnswer]]:
        if not question_ids:
            return {}
        placeholders = ",".join("?" for _ in question_ids)
        answer_rows = connection.execute(
            f"""
            SELECT id, question_id, text, is_correct
            FROM answers
            WHERE question_id IN ({placeholders})
            ORDER BY question_id ASC, position ASC
            """,
            question_ids,
        ).fetchall()
        answers_by_question: dict[int, list[DbAnswer]] = {}
        for answer_row in answer_rows:
            question_id = int(answer_row["question_id"])
            answers_by_question.setdefault(question_id, []).append(
                DbAnswer(
                    id=int(answer_row["id"]),
                    text=str(answer_row["text"]),
                    is_correct=bool(answer_row["is_correct"]),
                )
            )
        return answers_by_question

    def save_attempt(
        self,
        test_id: int,
        user_id: int,
        started_at: str,
        choices: list[AttemptChoice],
    ) -> tuple[int, int, int, float]:
        total_count = len(choices)
        correct_count = sum(1 for choice in choices if choice.is_correct)
        percent = round((correct_count / total_count) * 100, 2) if total_count else 0.0
        finished_at = _utc_now()

        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO attempts (
                    test_id, user_id, started_at, finished_at, correct_count, total_count, percent
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    test_id,
                    user_id,
                    started_at,
                    finished_at,
                    correct_count,
                    total_count,
                    percent,
                ),
            )
            attempt_id = int(cursor.lastrowid)

            if choices:
                placeholders = ",".join("(?, ?, ?, ?, ?)" for _ in choices)
                params: list[int] = []
                for choice in choices:
                    params.extend(
                        (
                            attempt_id,
                            choice.question_id,
                            choice.selected_answer_id,
                            choice.correct_answer_id,
                            1 if choice.is_correct else 0,
                        )
                    )
                connection.execute(
                    f"""
                    INSERT INTO attempt_answers (
                        attempt_id, question_id, selected_answer_id, correct_answer_id, is_correct
                    )
                    VALUES {placeholders}
                    """,
                    params,
                )

        return attempt_id, correct_count, total_count, percent

    def list_recent_attempts_with_mistakes(
        self,
        user_id: int,
        limit: int = 10,
    ) -> list[sqlite3.Row]:
        with self._connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        attempts.id,
                        attempts.test_id,
                        attempts.correct_count,
                        attempts.total_count,
                        attempts.percent,
                        attempts.finished_at,
                        tests.title
                    FROM attempts
                    JOIN tests ON tests.id = attempts.test_id
                    WHERE attempts.user_id = ?
                      AND attempts.correct_count < attempts.total_count
                    ORDER BY attempts.finished_at DESC, attempts.id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                )
            )

    def get_attempt_mistakes(self, attempt_id: int, user_id: int) -> list[sqlite3.Row]:
        with self._connection() as connection:
            return list(
                connection.execute(
                    """
                    SELECT
                        questions.text AS question_text,
                        selected_answers.text AS selected_answer,
                        correct_answers.text AS correct_answer
                    FROM attempt_answers
                    JOIN attempts ON attempts.id = attempt_answers.attempt_id
                    JOIN questions ON questions.id = attempt_answers.question_id
                    JOIN answers AS selected_answers
                      ON selected_answers.id = attempt_answers.selected_answer_id
                    JOIN answers AS correct_answers
                      ON correct_answers.id = attempt_answers.correct_answer_id
                    WHERE attempt_answers.attempt_id = ?
                      AND attempts.user_id = ?
                      AND attempt_answers.is_correct = 0
                    ORDER BY attempt_answers.id ASC
                    """,
                    (attempt_id, user_id),
                )
            )

    @contextmanager
    def _connection(self) -> Any:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self) -> Any:
        if self.is_postgres:
            pool = self._postgres_connection_pool()
            return _PostgresConnection(pool.getconn(), pool)
        assert self.path is not None
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _postgres_connection_pool(self) -> Any:
        if self._postgres_pool is None:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool

            pool_size = max(1, int(os.getenv("EXAM_BOT_DB_POOL_SIZE", "5")))
            self._postgres_pool = ConnectionPool(
                conninfo=self.location,
                min_size=1,
                max_size=pool_size,
                kwargs={"row_factory": dict_row},
                check=ConnectionPool.check_connection,
                open=True,
            )
        return self._postgres_pool

    def close(self) -> None:
        if self._postgres_pool is not None:
            self._postgres_pool.close()
            self._postgres_pool = None

    def _repair_rtf_fallback_artifacts(self, connection: sqlite3.Connection) -> None:
        for table, column in (
            ("tests", "title"),
            ("questions", "text"),
            ("answers", "text"),
        ):
            connection.execute(
                f"""
                UPDATE {table}
                SET {column} = replace({column}, '''5f', '')
                WHERE {column} LIKE '%''5f%'
                """
            )

    def _ensure_column(
        self,
        connection: Any,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        if self.is_postgres:
            row = connection.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = ? AND column_name = ?
                """,
                (table, column),
            ).fetchone()
            if row is None:
                connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            return

        columns = {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


class _PostgresRow(dict):
    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _PostgresCursor:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor
        self.lastrowid: int | None = None

    @property
    def rowcount(self) -> int:
        return int(self.cursor.rowcount)

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> "_PostgresCursor":
        sql = _postgres_sql(sql)
        table = _insert_returning_table(sql)
        if table:
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        self.cursor.execute(sql, tuple(params))
        if table:
            row = self.cursor.fetchone()
            self.lastrowid = int(row["id"])
        return self

    def fetchone(self) -> _PostgresRow | None:
        row = self.cursor.fetchone()
        return _PostgresRow(row) if row is not None else None

    def fetchall(self) -> list[_PostgresRow]:
        return [_PostgresRow(row) for row in self.cursor.fetchall()]


class _PostgresConnection:
    def __init__(self, connection: Any, pool: Any) -> None:
        self.connection = connection
        self.pool = pool
        self.closed = False

    def execute(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] = (),
    ) -> _PostgresCursor:
        cursor = _PostgresCursor(self.connection.cursor())
        return cursor.execute(sql, params)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        if not self.closed:
            self.pool.putconn(self.connection)
            self.closed = True


def _postgres_sql(sql: str) -> str:
    converted = sql.replace("%", "%%").replace("?", "%s")
    converted = converted.replace("round(avg(percent), 2)", "round(avg(percent)::numeric, 2)")
    return converted


def _insert_returning_table(sql: str) -> str | None:
    normalized = " ".join(sql.lower().split())
    if " returning " in f" {normalized} ":
        return None
    for table in ("tests", "questions", "attempts"):
        if normalized.startswith(f"insert into {table} "):
            return table
    return None


def _is_integrity_error(exc: Exception) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    return exc.__class__.__name__ in {"IntegrityError", "UniqueViolation"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
