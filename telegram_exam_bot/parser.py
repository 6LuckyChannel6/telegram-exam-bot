from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
import zipfile
import xml.etree.ElementTree as ET


SUPPORTED_EXTENSIONS = {".docx", ".rtf", ".txt"}


class TestParseError(ValueError):
    """Raised when an uploaded test cannot be parsed."""

    __test__ = False


@dataclass(frozen=True)
class ParsedQuestion:
    text: str
    answers: tuple[str, ...]
    correct_answer_index: int = 0


@dataclass(frozen=True)
class ParsedTest:
    title: str
    questions: tuple[ParsedQuestion, ...]


_QUESTION_RE = re.compile(r"^\s*#{3,}\s*(.*?)\s*$")
_ANSWER_RE = re.compile(r"^\s*\?{3,}\s*(.*?)\s*$")
_PLAIN_QUESTION_RE = re.compile(
    r"^\s*(?:(?:вопрос|сұрақ|question)\s*)?(?:№\s*)?(\d{1,4})([\).:-])\s+(.+?)\s*$",
    re.IGNORECASE,
)
_PLAIN_ANSWER_RE = re.compile(
    r"^\s*(?:[A-Ha-hА-Еа-еӘәБбВвГгДдЕе]|[1-9]\d?)[\).:-]\s+(.+?)\s*$"
)
_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def parse_test_file(filename: str, payload: bytes) -> ParsedTest:
    extension = Path(filename).suffix.lower()
    fallback_title = _clean_title(Path(filename).stem)

    if extension == ".docx":
        lines = _extract_docx_lines(payload)
    elif extension == ".rtf":
        lines = _extract_rtf_text(payload).splitlines()
    elif extension == ".txt":
        lines = _decode_text(payload).splitlines()
    else:
        raise TestParseError(
            "Поддерживаются только .docx, .rtf и .txt файлы с вопросами #####/?????."
        )

    if _has_marked_format(lines):
        return parse_marked_lines(lines, fallback_title=fallback_title)
    return parse_plain_lines(lines, fallback_title=fallback_title)


def parse_marked_lines(lines: list[str], fallback_title: str = "Тест") -> ParsedTest:
    title_lines: list[str] = []
    questions: list[ParsedQuestion] = []
    current_question: str | None = None
    current_answers: list[str] = []
    broken_questions: list[str] = []

    def flush_current() -> None:
        nonlocal current_question, current_answers
        if current_question is None:
            return

        question = _normalize_text(current_question)
        answers = [_normalize_text(answer) for answer in current_answers if answer.strip()]

        if question and len(answers) >= 2:
            questions.append(
                ParsedQuestion(
                    text=question,
                    answers=tuple(answers),
                    correct_answer_index=0,
                )
            )
        elif question:
            broken_questions.append(question[:90])

        current_question = None
        current_answers = []

    for raw_line in lines:
        line = _normalize_text(raw_line)
        if not line:
            continue

        question_match = _QUESTION_RE.match(line)
        if question_match:
            flush_current()
            current_question = question_match.group(1).strip()
            current_answers = []
            continue

        answer_match = _ANSWER_RE.match(line)
        if answer_match:
            if current_question is None:
                continue
            current_answers.append(answer_match.group(1).strip())
            continue

        if current_question is None:
            title_lines.append(line)
        elif current_answers:
            current_answers[-1] = f"{current_answers[-1]} {line}".strip()
        else:
            current_question = f"{current_question} {line}".strip()

    flush_current()

    if not questions:
        raise TestParseError(
            "Не нашел вопросов. Вопросы должны начинаться с ##### , варианты - с ?????."
        )

    if broken_questions:
        examples = "; ".join(broken_questions[:3])
        raise TestParseError(
            "Некоторые вопросы без минимум двух вариантов ответа. Проверьте: "
            f"{examples}"
        )

    title = _clean_title(title_lines[0] if title_lines else fallback_title)
    return ParsedTest(title=title, questions=tuple(questions))


def parse_plain_lines(lines: list[str], fallback_title: str = "Тест") -> ParsedTest:
    title_lines: list[str] = []
    questions: list[ParsedQuestion] = []
    current_question: str | None = None
    current_number: int | None = None
    current_answers: list[str] = []
    broken_questions: list[str] = []

    def flush_current() -> None:
        nonlocal current_question, current_number, current_answers
        if current_question is None:
            return

        question = _normalize_text(current_question)
        answers = [_normalize_text(answer) for answer in current_answers if answer.strip()]
        if question and len(answers) >= 2:
            questions.append(ParsedQuestion(text=question, answers=tuple(answers)))
        elif question:
            broken_questions.append(question[:90])

        current_question = None
        current_number = None
        current_answers = []

    for raw_line in lines:
        line = _normalize_text(raw_line)
        if not line:
            continue

        question_match = _PLAIN_QUESTION_RE.match(line)
        answer_match = _PLAIN_ANSWER_RE.match(line)

        if current_question is None:
            if question_match:
                current_number = int(question_match.group(1))
                current_question = question_match.group(3).strip()
            else:
                title_lines.append(line)
            continue

        if (
            question_match
            and current_answers
            and _looks_like_next_question(
                number=int(question_match.group(1)),
                separator=question_match.group(2),
                previous_number=current_number,
                text=question_match.group(3),
            )
        ):
            flush_current()
            current_number = int(question_match.group(1))
            current_question = question_match.group(3).strip()
            continue

        if answer_match:
            current_answers.append(answer_match.group(1).strip())
            continue

        if current_answers:
            current_answers[-1] = f"{current_answers[-1]} {line}".strip()
        else:
            current_question = f"{current_question} {line}".strip()

    flush_current()

    if not questions:
        raise TestParseError(
            "Не нашел вопросы. Для обычного теста используйте нумерацию вопросов и вариантов: "
            "1. Вопрос, A) правильный ответ, B) другой вариант."
        )

    if broken_questions:
        examples = "; ".join(broken_questions[:3])
        raise TestParseError(
            "Некоторые вопросы без минимум двух вариантов ответа. Проверьте: "
            f"{examples}"
        )

    title = _clean_title(title_lines[0] if title_lines else fallback_title)
    return ParsedTest(title=title, questions=tuple(questions))


def _has_marked_format(lines: list[str]) -> bool:
    return any(_QUESTION_RE.match(_normalize_text(line)) for line in lines) or any(
        _ANSWER_RE.match(_normalize_text(line)) for line in lines
    )


def _looks_like_next_question(
    number: int,
    separator: str,
    previous_number: int | None,
    text: str,
) -> bool:
    if previous_number is not None and number == previous_number + 1:
        if separator == ")":
            return _normalize_text(text).endswith(("?", ":"))
        return _looks_like_question_text(text)
    return False


def _looks_like_question_text(text: str) -> bool:
    normalized = _normalize_text(text)
    return normalized.endswith(("?", ":")) or len(normalized.split()) >= 3


def _extract_docx_lines(payload: bytes) -> list[str]:
    try:
        with zipfile.ZipFile(BytesIO(payload)) as archive:
            document_xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile) as exc:
        raise TestParseError("Не получилось прочитать .docx файл.") from exc

    root = ET.fromstring(document_xml)
    lines: list[str] = []

    for paragraph in root.iter(f"{_WORD_NS}p"):
        parts: list[str] = []
        for node in paragraph.iter():
            if node.tag == f"{_WORD_NS}t":
                parts.append(node.text or "")
            elif node.tag == f"{_WORD_NS}tab":
                parts.append(" ")
            elif node.tag == f"{_WORD_NS}br":
                parts.append("\n")

        text = _normalize_text("".join(parts))
        if text:
            lines.append(text)

    return lines


def _decode_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise TestParseError("Не получилось прочитать текстовый файл. Сохраните его в UTF-8.")


def _extract_rtf_text(payload: bytes) -> str:
    source = _decode_text(payload)
    if not source.lstrip().startswith("{\\rtf"):
        raise TestParseError("Файл не похож на RTF-документ.")

    result: list[str] = []
    ignorable_stack: list[bool] = []
    ignorable = False
    pending_unicode_skip = 1
    index = 0

    while index < len(source):
        char = source[index]

        if char == "{":
            ignorable_stack.append(ignorable)
            index += 1
            continue

        if char == "}":
            ignorable = ignorable_stack.pop() if ignorable_stack else False
            index += 1
            continue

        if char != "\\":
            if not ignorable and char not in "\r\n":
                result.append(char)
            index += 1
            continue

        index += 1
        if index >= len(source):
            break

        escaped = source[index]
        if escaped in "\\{}":
            if not ignorable:
                result.append(escaped)
            index += 1
            continue

        if escaped in "\r\n":
            index += 1
            if escaped == "\r" and index < len(source) and source[index] == "\n":
                index += 1
            continue

        if escaped == "*":
            ignorable = True
            index += 1
            continue

        if escaped == "'":
            hex_value = source[index + 1 : index + 3]
            if len(hex_value) == 2:
                try:
                    if not ignorable:
                        result.append(bytes.fromhex(hex_value).decode("cp1251"))
                    index += 3
                    continue
                except (ValueError, UnicodeDecodeError):
                    pass

        match = re.match(r"([A-Za-z]+)(-?\d+)? ?", source[index:])
        if not match:
            index += 1
            continue

        word = match.group(1)
        argument = match.group(2)
        index += len(match.group(0))

        if word in {"fonttbl", "colortbl", "stylesheet", "info", "pict", "object"}:
            ignorable = True
            continue

        if ignorable:
            continue

        if word in {"par", "line"}:
            result.append("\n")
        elif word == "tab":
            result.append(" ")
        elif word == "emdash":
            result.append("-")
        elif word == "endash":
            result.append("-")
        elif word == "bullet":
            result.append("*")
        elif word == "uc" and argument is not None:
            pending_unicode_skip = max(0, int(argument))
        elif word == "u" and argument is not None:
            value = int(argument)
            if value < 0:
                value += 65536
            result.append(chr(value))
            index = _skip_unicode_fallback(source, index, pending_unicode_skip)

    return "".join(result)


def _skip_unicode_fallback(source: str, index: int, count: int) -> int:
    for _ in range(count):
        if index >= len(source):
            return index

        if source.startswith("\\'", index) and _is_hex_pair(source[index + 2 : index + 4]):
            index += 4
            continue

        if source[index] == "\\":
            if index + 1 >= len(source):
                return index + 1

            escaped = source[index + 1]
            if escaped in "\\{}":
                index += 2
                continue

            match = re.match(r"\\[A-Za-z]+-?\d* ?", source[index:])
            if match:
                index += len(match.group(0))
                continue

            index += 2
            continue

        index += 1

    return index


def _is_hex_pair(value: str) -> bool:
    return len(value) == 2 and all(char in "0123456789abcdefABCDEF" for char in value)


def _normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def _clean_title(value: str) -> str:
    title = _normalize_text(value) or "Тест"
    return title[:80]
