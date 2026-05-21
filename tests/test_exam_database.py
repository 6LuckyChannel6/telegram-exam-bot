from telegram_exam_bot.database import AttemptChoice, Database, _PostgresCursor
from telegram_exam_bot.parser import parse_marked_lines


def test_database_saves_private_test_and_questions(tmp_path):
    database = Database(tmp_path / "bot.sqlite3")
    database.initialize()
    parsed = parse_marked_lines(
        [
            "TITLE",
            "##### Q1",
            "????? correct",
            "????? wrong",
        ],
        fallback_title="fallback",
    )

    test_id = database.create_test(42, "sample.txt", parsed)

    assert database.get_test_for_attempt(test_id, 99) is None
    saved = database.get_test_for_attempt(test_id, 42)
    assert saved is not None
    assert saved.title == "TITLE"
    assert saved.question_count == 1
    assert saved.questions[0].answers[0].text == "correct"
    assert saved.questions[0].answers[0].is_correct is True


def test_database_saves_attempt_and_mistakes(tmp_path):
    database = Database(tmp_path / "bot.sqlite3")
    database.initialize()
    parsed = parse_marked_lines(
        [
            "TITLE",
            "##### Q1",
            "????? correct",
            "????? wrong",
        ],
        fallback_title="fallback",
    )
    test_id = database.create_test(42, "sample.txt", parsed)
    test = database.get_test_for_attempt(test_id, 42)
    question = test.questions[0]
    correct = next(answer for answer in question.answers if answer.is_correct)
    wrong = next(answer for answer in question.answers if not answer.is_correct)

    attempt_id, correct_count, total_count, percent = database.save_attempt(
        test_id,
        42,
        "2026-05-18T00:00:00+00:00",
        [
            AttemptChoice(
                question_id=question.id,
                selected_answer_id=wrong.id,
                correct_answer_id=correct.id,
                is_correct=False,
            )
        ],
    )

    assert correct_count == 0
    assert total_count == 1
    assert percent == 0.0
    mistakes = database.get_attempt_mistakes(attempt_id, 42)
    assert len(mistakes) == 1
    assert mistakes[0]["selected_answer"] == "wrong"
    assert mistakes[0]["correct_answer"] == "correct"


def test_database_saves_user_language(tmp_path):
    database = Database(tmp_path / "bot.sqlite3")
    database.initialize()

    assert database.get_user_language(42) == "ru"

    database.set_user_language(42, "kk")

    assert database.get_user_language(42) == "kk"


def test_database_users_settings_and_admin_stats(tmp_path):
    database = Database(tmp_path / "bot.sqlite3")
    database.initialize()

    database.upsert_user(42, "student", "First", "Last")
    database.set_user_blocked(42, True)
    database.set_setting("paused", "1")
    parsed = parse_marked_lines(
        [
            "TITLE",
            "##### Q1",
            "????? correct",
            "????? wrong",
        ],
        fallback_title="fallback",
    )
    database.create_test(42, "sample.txt", parsed)

    stats = database.admin_stats()

    assert database.get_setting("paused") == "1"
    assert database.is_user_blocked(42) is True
    assert stats["users"] == 1
    assert stats["blocked_users"] == 1
    assert stats["tests"] == 1
    assert database.admin_recent_tests()[0]["username"] == "student"
    assert database.admin_get_user(42)["test_count"] == 1
    assert database.admin_user_tests(42)[0]["title"] == "TITLE"
    assert database.admin_get_test(1).questions[0].text == "Q1"


def test_database_shares_and_copies_test(tmp_path):
    database = Database(tmp_path / "bot.sqlite3")
    database.initialize()
    parsed = parse_marked_lines(
        [
            "TITLE",
            "##### Q1",
            "????? correct",
            "????? wrong",
        ],
        fallback_title="fallback",
    )
    source_test_id = database.create_test(42, "sample.txt", parsed)

    token = database.get_or_create_share_token(source_test_id, 42)
    assert token
    assert database.get_or_create_share_token(source_test_id, 99) is None

    summary = database.get_shared_test_summary(token)
    assert summary["title"] == "TITLE"
    assert summary["question_count"] == 1

    copied_test_id = database.copy_shared_test_to_owner(token, 99)
    copied = database.get_test_for_attempt(copied_test_id, 99)

    assert copied.title == "TITLE"
    assert copied.source_test_id == source_test_id
    assert copied.questions[0].text == "Q1"
    assert copied.questions[0].answers[0].text == "correct"
    assert database.copy_shared_test_to_owner("bad-token", 99) is None


def test_database_delete_test_for_all_removes_linked_copies(tmp_path):
    database = Database(tmp_path / "bot.sqlite3")
    database.initialize()
    parsed = parse_marked_lines(
        [
            "TITLE",
            "##### Q1",
            "????? correct",
            "????? wrong",
        ],
        fallback_title="fallback",
    )
    source_test_id = database.create_test(42, "sample.txt", parsed)
    token = database.get_or_create_share_token(source_test_id, 42)
    copied_test_id = database.copy_shared_test_to_owner(token, 99)

    deleted = database.delete_test_for_all(source_test_id, 42)

    assert set(deleted) == {source_test_id, copied_test_id}
    assert database.get_test_for_attempt(source_test_id, 42) is None
    assert database.get_test_for_attempt(copied_test_id, 99) is None


def test_database_admin_delete_foreign_tests(tmp_path):
    database = Database(tmp_path / "bot.sqlite3")
    database.initialize()
    parsed = parse_marked_lines(
        [
            "TITLE",
            "##### Q1",
            "????? correct",
            "????? wrong",
        ],
        fallback_title="fallback",
    )
    source_test_id = database.create_test(42, "sample.txt", parsed)
    token = database.get_or_create_share_token(source_test_id, 42)
    copied_test_id = database.copy_shared_test_to_owner(token, 99)

    assert database.admin_delete_test(copied_test_id) is True
    assert database.get_test_for_attempt(copied_test_id, 99) is None
    assert database.get_test_for_attempt(source_test_id, 42) is not None

    token = database.get_or_create_share_token(source_test_id, 42)
    copied_test_id = database.copy_shared_test_to_owner(token, 99)
    deleted = database.admin_delete_test_for_all(copied_test_id)

    assert set(deleted) == {source_test_id, copied_test_id}
    assert database.get_test_for_attempt(source_test_id, 42) is None
    assert database.get_test_for_attempt(copied_test_id, 99) is None


def test_database_mistake_questions_for_test(tmp_path):
    database = Database(tmp_path / "bot.sqlite3")
    database.initialize()
    parsed = parse_marked_lines(
        [
            "TITLE",
            "##### Q1",
            "????? correct",
            "????? wrong",
        ],
        fallback_title="fallback",
    )
    test_id = database.create_test(42, "sample.txt", parsed)
    test = database.get_test_for_attempt(test_id, 42)
    question = test.questions[0]
    correct = next(answer for answer in question.answers if answer.is_correct)
    wrong = next(answer for answer in question.answers if not answer.is_correct)
    database.save_attempt(
        test_id,
        42,
        "2026-05-18T00:00:00+00:00",
        [AttemptChoice(question.id, wrong.id, correct.id, False)],
    )

    mistakes = database.get_mistake_questions_for_test(test_id, 42)

    assert len(mistakes) == 1
    assert mistakes[0].text == "Q1"


def test_postgres_cursor_can_be_iterated_like_sqlite_cursor():
    class FakeCursor:
        rowcount = 2

        def execute(self, sql, params):
            self.sql = sql
            self.params = params

        def fetchall(self):
            return [{"id": 1}, {"id": 2}]

    cursor = _PostgresCursor(FakeCursor()).execute("SELECT id FROM tests WHERE owner_id = ?", (42,))

    assert list(cursor) == [{"id": 1}, {"id": 2}]
