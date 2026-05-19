import pytest

from telegram_exam_bot.parser import (
    TestParseError,
    parse_marked_lines,
    parse_plain_lines,
    parse_test_file,
)


def test_parse_marked_lines_first_answer_is_correct():
    parsed = parse_marked_lines(
        [
            "САЯСАТТАНУ",
            "##### Бірінші сұрақ?",
            "????? Дұрыс жауап",
            "????? Қате жауап 1",
            "????? Қате жауап 2",
            "##### Екінші сұрақ?",
            "????? A",
            "????? B",
        ],
        fallback_title="fallback",
    )

    assert parsed.title == "САЯСАТТАНУ"
    assert len(parsed.questions) == 2
    assert parsed.questions[0].text == "Бірінші сұрақ?"
    assert parsed.questions[0].answers[0] == "Дұрыс жауап"
    assert parsed.questions[0].correct_answer_index == 0


def test_parse_marked_lines_keeps_continuation_lines():
    parsed = parse_marked_lines(
        [
            "##### Ұзақ сұрақ",
            "жалғасы",
            "????? Дұрыс",
            "жауап",
            "????? Қате",
        ],
        fallback_title="Тест",
    )

    question = parsed.questions[0]
    assert question.text == "Ұзақ сұрақ жалғасы"
    assert question.answers[0] == "Дұрыс жауап"


def test_parse_marked_lines_rejects_questions_without_two_answers():
    with pytest.raises(TestParseError):
        parse_marked_lines(
            [
                "##### Сұрақ?",
                "????? Тек бір жауап",
            ],
            fallback_title="Тест",
        )


def test_parse_plain_lines_with_letter_answers():
    parsed = parse_plain_lines(
        [
            "World history",
            "1. Capital of Kazakhstan?",
            "A) Astana",
            "B) Almaty",
            "C) Shymkent",
            "2. Capital of France?",
            "A) Paris",
            "B) Lyon",
        ],
        fallback_title="fallback",
    )

    assert parsed.title == "World history"
    assert len(parsed.questions) == 2
    assert parsed.questions[0].text == "Capital of Kazakhstan?"
    assert parsed.questions[0].answers[0] == "Astana"
    assert parsed.questions[1].answers == ("Paris", "Lyon")


def test_parse_plain_lines_with_numeric_answers():
    parsed = parse_plain_lines(
        [
            "1. Қазақ тіліндегі дұрыс жауап?",
            "1) Бірінші жауап дұрыс",
            "2) Екінші жауап қате",
            "3) Үшінші жауап қате",
            "2. Келесі сұрақ?",
            "1) Дұрыс",
            "2) Қате",
        ],
        fallback_title="Тест",
    )

    assert len(parsed.questions) == 2
    assert parsed.questions[0].answers[0] == "Бірінші жауап дұрыс"
    assert parsed.questions[0].answers[1] == "Екінші жауап қате"


def test_parse_rtf_file():
    payload = (
        r"{\rtf1\ansi\uc1 TITLE\par "
        r"##### Q1?\par "
        r"????? Correct\par "
        r"????? Wrong\par "
        r"}"
    ).encode("utf-8")

    parsed = parse_test_file("sample.rtf", payload)

    assert parsed.title == "TITLE"
    assert parsed.questions[0].text == "Q1?"
    assert parsed.questions[0].answers == ("Correct", "Wrong")


def test_parse_rtf_skips_unicode_fallback_hex():
    payload = (
        r"{\rtf1\ansi\ansicpg1252\uc1 "
        r"\'cc\u1240\'5f\'c4\'c5\'cd\'c8\'c5\'d2\'d2\'c0\'cd\'d3\par "
        r"##### Q1?\par "
        r"????? \u1178\'5f\u1201\'5f answer\par "
        r"????? Wrong\par "
        r"}"
    ).encode("utf-8")

    parsed = parse_test_file("kazakh.rtf", payload)

    assert parsed.title == "МӘДЕНИЕТТАНУ"
    assert parsed.questions[0].answers[0] == "Құ answer"
    assert "'5f" not in parsed.questions[0].answers[0]
