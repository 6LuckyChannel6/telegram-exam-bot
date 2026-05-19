from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
from io import BytesIO
import logging
import os
from pathlib import Path
import random
import re
import sys
import time
from textwrap import shorten

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError
from aiogram.filters import BaseFilter, Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from .database import AttemptChoice, Database, DbAnswer, DbQuestion
from .parser import SUPPORTED_EXTENSIONS, TestParseError, parse_test_file


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
MODE_TRAINING = "training"
MODE_MISTAKES = "mistakes"
QUESTION_SIZES = ("10", "20", "50", "all")
TIMER_OPTIONS = (0, 5, 10, 20, 30, 60)
TIMER_REFRESH_SECONDS = 5
TOKEN_RE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{20,}$")
PLACEHOLDER_TOKEN_PARTS = ("replace", "your_real_token", "example", "botfather")
LANGUAGES = {"ru", "kk", "en"}
LANGUAGE_NAMES = {
    "ru": "Русский",
    "kk": "Қазақша",
    "en": "English",
}

TEXT = {
    "home_title": {
        "ru": "Главное меню",
        "kk": "Басты мәзір",
        "en": "Main menu",
    },
    "home_desc": {
        "ru": "Загрузите тест, выберите режим и проходите пробник. Вопросы и варианты перемешиваются автоматически.",
        "kk": "Тестті жүктеп, режимді таңдаңыз және пробниктен өтіңіз. Сұрақтар мен жауаптар автоматты түрде араласады.",
        "en": "Upload a test, choose a mode, and start practice. Questions and answers are shuffled automatically.",
    },
    "upload_text": {
        "ru": "Отправьте файл с тестом в этот чат.\n\nПоддерживаются .docx, .rtf и .txt. После загрузки бот сохранит тест и соберет пробник для самопроверки.",
        "kk": "Тест файлын осы чатқа жіберіңіз.\n\n.docx, .rtf және .txt файлдары қолдау табады. Жүктегеннен кейін бот тестті сақтап, өзін-өзі тексеруге пробник жасайды.",
        "en": "Send the test file to this chat.\n\n.docx, .rtf, and .txt are supported. After upload, the bot will save the test and build a practice exam.",
    },
    "language_title": {
        "ru": "Выберите язык интерфейса:",
        "kk": "Интерфейс тілін таңдаңыз:",
        "en": "Choose interface language:",
    },
    "language_saved": {
        "ru": "Язык изменен.",
        "kk": "Тіл өзгертілді.",
        "en": "Language changed.",
    },
    "active_none": {
        "ru": "Активного пробника нет.",
        "kk": "Белсенді пробник жоқ.",
        "en": "No active practice exam.",
    },
    "unsupported_format": {
        "ru": "Поддерживаются только .docx, .rtf и .txt файлы.",
        "kk": "Тек .docx, .rtf және .txt файлдары қолдау табады.",
        "en": "Only .docx, .rtf, and .txt files are supported.",
    },
    "too_big": {
        "ru": "Файл слишком большой. Лимит: 10 МБ.",
        "kk": "Файл тым үлкен. Шектеу: 10 МБ.",
        "en": "The file is too large. Limit: 10 MB.",
    },
    "file_received": {
        "ru": "Файл получен. Разбираю тест...",
        "kk": "Файл алынды. Тест талданып жатыр...",
        "en": "File received. Parsing the test...",
    },
    "parse_fail": {
        "ru": "Не смог разобрать тест: {error}",
        "kk": "Тестті талдай алмадым: {error}",
        "en": "Could not parse the test: {error}",
    },
    "unexpected_upload": {
        "ru": "Произошла ошибка при обработке файла.",
        "kk": "Файлды өңдеу кезінде қате пайда болды.",
        "en": "An error occurred while processing the file.",
    },
    "test_added": {
        "ru": "Тест добавлен.",
        "kk": "Тест қосылды.",
        "en": "Test added.",
    },
    "test_card": {
        "ru": "Карточка теста",
        "kk": "Тест картасы",
        "en": "Test card",
    },
    "delete_prompt": {
        "ru": "Удалить тест «{title}»?\n\nУдалятся вопросы, варианты и история попыток по этому тесту.",
        "kk": "«{title}» тестін өшіру керек пе?\n\nОсы тесттің сұрақтары, жауаптары және әрекеттер тарихы өшеді.",
        "en": "Delete test \"{title}\"?\n\nQuestions, answers, and attempt history for this test will be deleted.",
    },
    "delete_all_prompt": {
        "ru": "Удалить тест «{title}» у всех?\n\nУдалится оригинал, все копии по ссылке и вся статистика по ним.",
        "kk": "«{title}» тестін бәрінен өшіру керек пе?\n\nОригинал, сілтеме арқылы алынған көшірмелер және статистика өшеді.",
        "en": "Delete \"{title}\" for everyone?\n\nThe original, linked copies, and related stats will be deleted.",
    },
    "deleted": {
        "ru": "Тест удален.",
        "kk": "Тест өшірілді.",
        "en": "Test deleted.",
    },
    "deleted_all": {
        "ru": "Тест удален у всех. Связанные копии тоже удалены.",
        "kk": "Тест бәрінен өшірілді. Байланысқан көшірмелер де өшті.",
        "en": "The test was deleted for everyone. Linked copies were deleted too.",
    },
    "not_found_test": {
        "ru": "Тест не найден.",
        "kk": "Тест табылмады.",
        "en": "Test not found.",
    },
    "already_deleted": {
        "ru": "Тест уже удален.",
        "kk": "Тест бұрын өшірілген.",
        "en": "The test has already been deleted.",
    },
    "empty_test": {
        "ru": "В этом тесте нет вопросов.",
        "kk": "Бұл тестте сұрақ жоқ.",
        "en": "This test has no questions.",
    },
    "inactive_attempt": {
        "ru": "Этот пробник уже не активен.",
        "kk": "Бұл пробник енді белсенді емес.",
        "en": "This practice exam is no longer active.",
    },
    "bad_answer": {
        "ru": "Некорректный ответ.",
        "kk": "Жауап дұрыс емес форматта.",
        "en": "Invalid answer.",
    },
    "old_button": {
        "ru": "Это кнопка от старого вопроса.",
        "kk": "Бұл алдыңғы сұрақтың батырмасы.",
        "en": "This button belongs to an old question.",
    },
    "answer_missing": {
        "ru": "Ответ не найден.",
        "kk": "Жауап табылмады.",
        "en": "Answer not found.",
    },
    "answer_saved": {
        "ru": "Ответ сохранен.",
        "kk": "Жауап сақталды.",
        "en": "Answer saved.",
    },
    "correct_feedback": {
        "ru": "Правильно",
        "kk": "Дұрыс",
        "en": "Correct",
    },
    "wrong_feedback": {
        "ru": "Неверно",
        "kk": "Қате",
        "en": "Incorrect",
    },
    "your_answer": {
        "ru": "Ваш ответ",
        "kk": "Сіздің жауабыңыз",
        "en": "Your answer",
    },
    "correct_answer": {
        "ru": "Правильный ответ",
        "kk": "Дұрыс жауап",
        "en": "Correct answer",
    },
    "next_question": {
        "ru": "Следующий вопрос",
        "kk": "Келесі сұрақ",
        "en": "Next question",
    },
    "attempt_finished": {
        "ru": "Пробник завершен",
        "kk": "Пробник аяқталды",
        "en": "Practice exam finished",
    },
    "attempt_timed_out": {
        "ru": "Время вышло",
        "kk": "Уақыт аяқталды",
        "en": "Time is up",
    },
    "attempt_stopped": {
        "ru": "Пробник завершен досрочно",
        "kk": "Пробник мерзімінен бұрын аяқталды",
        "en": "Practice exam stopped early",
    },
    "not_saved_no_answers": {
        "ru": "Ответов пока нет, поэтому попытка не сохранена.",
        "kk": "Әзірге жауап жоқ, сондықтан әрекет сақталмады.",
        "en": "No answers yet, so the attempt was not saved.",
    },
    "stopped_note": {
        "ru": "Итог посчитан по тем вопросам, на которые вы уже ответили.",
        "kk": "Қорытынды тек жауап берілген сұрақтар бойынша есептелді.",
        "en": "The result is calculated only from answered questions.",
    },
    "no_mistakes": {
        "ru": "Пока нет попыток с ошибками.",
        "kk": "Әзірге қате жіберілген әрекеттер жоқ.",
        "en": "No attempts with mistakes yet.",
    },
    "attempt_without_mistakes": {
        "ru": "В этой попытке ошибок нет.",
        "kk": "Бұл әрекетте қате жоқ.",
        "en": "This attempt has no mistakes.",
    },
    "choose_attempt": {
        "ru": "Выберите попытку для разбора:",
        "kk": "Талдау үшін әрекетті таңдаңыз:",
        "en": "Choose an attempt to review:",
    },
    "mistakes_header": {
        "ru": "Работа над ошибками",
        "kk": "Қателерді талдау",
        "en": "Mistake review",
    },
    "mistakes_done": {
        "ru": "Разбор завершен.",
        "kk": "Талдау аяқталды.",
        "en": "Review complete.",
    },
    "no_tests": {
        "ru": "У вас пока нет сохраненных тестов.\n\nНажмите «Добавить тест» и отправьте файл.",
        "kk": "Сақталған тесттер әлі жоқ.\n\n«Тест қосу» батырмасын басып, файл жіберіңіз.",
        "en": "You do not have saved tests yet.\n\nTap \"Add test\" and send a file.",
    },
    "choose_test": {
        "ru": "Выберите тест для пробника:",
        "kk": "Пробник үшін тест таңдаңыз:",
        "en": "Choose a test for practice:",
    },
    "choose_mode": {
        "ru": "Выберите режим:",
        "kk": "Режимді таңдаңыз:",
        "en": "Choose mode:",
    },
    "choose_size": {
        "ru": "Сколько вопросов взять?",
        "kk": "Қанша сұрақ алу керек?",
        "en": "How many questions?",
    },
    "choose_timer": {
        "ru": "Выберите таймер:",
        "kk": "Таймерді таңдаңыз:",
        "en": "Choose timer:",
    },
    "no_mistake_questions": {
        "ru": "По этому тесту пока нет ошибок для повторения.",
        "kk": "Бұл тест бойынша қайталайтын қателер әлі жоқ.",
        "en": "There are no mistakes to practice for this test yet.",
    },
    "timer_label": {"ru": "Таймер", "kk": "Таймер", "en": "Timer"},
    "timer_left": {"ru": "Осталось", "kk": "Қалды", "en": "Left"},
    "progress_label": {"ru": "Прогресс", "kk": "Прогресс", "en": "Progress"},
    "mode_label": {"ru": "Режим", "kk": "Режим", "en": "Mode"},
    "mode_training": {"ru": "Тренировка", "kk": "Жаттығу", "en": "Training"},
    "mode_mistakes": {"ru": "Ошибки", "kk": "Қателер", "en": "Mistakes"},
    "grade_excellent": {"ru": "Отлично", "kk": "Өте жақсы", "en": "Excellent"},
    "grade_good": {"ru": "Хорошо", "kk": "Жақсы", "en": "Good"},
    "grade_repeat": {"ru": "Нужно повторить", "kk": "Қайталау керек", "en": "Needs review"},
    "grade_weak": {"ru": "Слабый результат", "kk": "Нәтиже әлсіз", "en": "Weak result"},
    "saved_tests": {
        "ru": "Сохраненные тесты:",
        "kk": "Сақталған тесттер:",
        "en": "Saved tests:",
    },
    "label_test": {"ru": "Тест", "kk": "Тест", "en": "Test"},
    "label_title": {"ru": "Название", "kk": "Атауы", "en": "Title"},
    "label_file": {"ru": "Файл", "kk": "Файл", "en": "File"},
    "label_questions": {"ru": "Вопросов", "kk": "Сұрақ саны", "en": "Questions"},
    "label_added": {"ru": "Добавлен", "kk": "Қосылған уақыты", "en": "Added"},
    "label_question": {"ru": "Вопрос", "kk": "Сұрақ", "en": "Question"},
    "label_answered": {"ru": "Отвечено", "kk": "Жауап берілді", "en": "Answered"},
    "label_correct": {"ru": "Правильно", "kk": "Дұрыс", "en": "Correct"},
    "label_percent": {"ru": "Процент", "kk": "Пайыз", "en": "Percent"},
    "label_users": {"ru": "Пользователи", "kk": "Қолданушылар", "en": "Users"},
    "label_blocked_users": {"ru": "Заблокированы", "kk": "Бұғатталған", "en": "Blocked"},
    "label_tests": {"ru": "Тесты", "kk": "Тесттер", "en": "Tests"},
    "label_attempts": {"ru": "Попытки", "kk": "Әрекеттер", "en": "Attempts"},
    "label_share_links": {"ru": "Ссылки", "kk": "Сілтемелер", "en": "Share links"},
    "btn_add_test": {"ru": "Добавить тест", "kk": "Тест қосу", "en": "Add test"},
    "btn_my_tests": {"ru": "Мои тесты", "kk": "Тесттерім", "en": "My tests"},
    "btn_new_attempt": {"ru": "Новый пробник", "kk": "Жаңа пробник", "en": "New practice"},
    "btn_mistakes": {"ru": "Разбор ошибок", "kk": "Қателерді талдау", "en": "Mistake review"},
    "btn_language": {"ru": "Язык", "kk": "Тіл", "en": "Language"},
    "btn_back": {"ru": "Назад", "kk": "Артқа", "en": "Back"},
    "btn_start_attempt": {"ru": "Начать пробник", "kk": "Пробникті бастау", "en": "Start practice"},
    "btn_delete": {"ru": "Удалить тест", "kk": "Тестті өшіру", "en": "Delete test"},
    "btn_delete_self": {"ru": "Удалить только у себя", "kk": "Тек өзімнен өшіру", "en": "Delete only for me"},
    "btn_delete_all": {"ru": "Удалить у всех", "kk": "Бәрінен өшіру", "en": "Delete for everyone"},
    "btn_confirm_delete": {"ru": "Да, удалить", "kk": "Иә, өшіру", "en": "Yes, delete"},
    "btn_cancel": {"ru": "Отмена", "kk": "Болдырмау", "en": "Cancel"},
    "btn_retry": {"ru": "Пройти еще раз", "kk": "Қайта өту", "en": "Try again"},
    "btn_restart": {"ru": "Начать заново", "kk": "Қайта бастау", "en": "Restart"},
    "btn_main_menu": {"ru": "Главное меню", "kk": "Басты мәзір", "en": "Main menu"},
    "btn_stop_result": {
        "ru": "Завершить и показать итог",
        "kk": "Аяқтап, қорытындыны көрсету",
        "en": "Finish and show result",
    },
    "btn_no_timer": {"ru": "Без таймера", "kk": "Таймерсіз", "en": "No timer"},
    "btn_all_questions": {"ru": "Все вопросы", "kk": "Барлық сұрақ", "en": "All questions"},
    "btn_share": {"ru": "Поделиться тестом", "kk": "Тестпен бөлісу", "en": "Share test"},
    "btn_open_shared": {"ru": "Открыть тест", "kk": "Тестті ашу", "en": "Open test"},
    "btn_copy_link": {"ru": "Открыть ссылку", "kk": "Сілтемені ашу", "en": "Open link"},
    "share_not_found": {
        "ru": "Ссылка на тест не найдена или тест удален.",
        "kk": "Тест сілтемесі табылмады немесе тест өшірілген.",
        "en": "The test link was not found or the test was deleted.",
    },
    "share_created": {
        "ru": "Ссылка для доступа к тесту:",
        "kk": "Тестке кіру сілтемесі:",
        "en": "Test access link:",
    },
    "share_hint": {
        "ru": "Отправьте эту ссылку студенту. Когда он откроет ее, тест скопируется в его список.",
        "kk": "Бұл сілтемені студентке жіберіңіз. Ол ашқанда тест оның тізіміне көшіріледі.",
        "en": "Send this link to a student. When opened, the test will be copied to their list.",
    },
    "shared_imported": {
        "ru": "Тест добавлен по ссылке.",
        "kk": "Тест сілтеме арқылы қосылды.",
        "en": "Test added from link.",
    },
    "shared_source": {
        "ru": "Вам поделились тестом.",
        "kk": "Сізге тест жіберілді.",
        "en": "A test was shared with you.",
    },
    "bot_paused": {
        "ru": "Бот временно выключен администратором.",
        "kk": "Ботты әкімші уақытша өшірді.",
        "en": "The bot is temporarily disabled by the administrator.",
    },
    "user_blocked": {
        "ru": "Ваш доступ к боту заблокирован администратором.",
        "kk": "Ботқа кіруді әкімші бұғаттады.",
        "en": "Your access to the bot has been blocked by the administrator.",
    },
    "admin_title": {"ru": "Админ-панель", "kk": "Әкімші панелі", "en": "Admin panel"},
    "admin_only": {"ru": "Нет доступа.", "kk": "Рұқсат жоқ.", "en": "Access denied."},
    "admin_paused": {"ru": "Бот выключен.", "kk": "Бот өшірілді.", "en": "Bot disabled."},
    "admin_resumed": {"ru": "Бот включен.", "kk": "Бот қосылды.", "en": "Bot enabled."},
    "admin_blocked": {"ru": "Пользователь заблокирован.", "kk": "Қолданушы бұғатталды.", "en": "User blocked."},
    "admin_unblocked": {"ru": "Пользователь разблокирован.", "kk": "Қолданушы бұғаттан шығарылды.", "en": "User unblocked."},
    "admin_delete_test_prompt": {
        "ru": "Удалить тест «{title}» у пользователя?\n\nУдалятся вопросы, ответы и попытки только этого теста.",
        "kk": "Қолданушыдан «{title}» тестін өшіру керек пе?\n\nТек осы тесттің сұрақтары, жауаптары және әрекеттері өшеді.",
        "en": "Delete \"{title}\" from the user?\n\nOnly this test's questions, answers, and attempts will be deleted.",
    },
    "admin_delete_all_prompt": {
        "ru": "Удалить тест «{title}» у всех?\n\nУдалятся оригинал, связанные копии и вся статистика по ним.",
        "kk": "«{title}» тестін бәрінен өшіру керек пе?\n\nОригинал, байланысқан көшірмелер және статистика өшеді.",
        "en": "Delete \"{title}\" for everyone?\n\nThe original, linked copies, and all related stats will be deleted.",
    },
    "admin_test_deleted": {"ru": "Тест удален.", "kk": "Тест өшірілді.", "en": "Test deleted."},
    "admin_test_deleted_all": {
        "ru": "Тест удален у всех.",
        "kk": "Тест бәрінен өшірілді.",
        "en": "Test deleted for everyone.",
    },
    "admin_cannot_block_admin": {
        "ru": "Админа нельзя заблокировать из панели.",
        "kk": "Әкімшіні панельден бұғаттауға болмайды.",
        "en": "Admins cannot be blocked from the panel.",
    },
    "admin_status_on": {"ru": "Статус: включен", "kk": "Күйі: қосулы", "en": "Status: enabled"},
    "admin_status_off": {"ru": "Статус: выключен", "kk": "Күйі: өшірулі", "en": "Status: disabled"},
    "admin_user_active": {"ru": "активен", "kk": "белсенді", "en": "active"},
    "admin_user_blocked": {"ru": "заблокирован", "kk": "бұғатталған", "en": "blocked"},
    "btn_admin_pause": {"ru": "Выключить бот", "kk": "Ботты өшіру", "en": "Disable bot"},
    "btn_admin_resume": {"ru": "Включить бот", "kk": "Ботты қосу", "en": "Enable bot"},
    "btn_admin_stats": {"ru": "Статистика", "kk": "Статистика", "en": "Stats"},
    "btn_admin_users": {"ru": "Пользователи", "kk": "Қолданушылар", "en": "Users"},
    "btn_admin_tests": {"ru": "Последние тесты", "kk": "Соңғы тесттер", "en": "Recent tests"},
    "btn_admin_attempts": {"ru": "Последние попытки", "kk": "Соңғы әрекеттер", "en": "Recent attempts"},
    "btn_admin_block": {"ru": "Заблокировать", "kk": "Бұғаттау", "en": "Block"},
    "btn_admin_unblock": {"ru": "Разблокировать", "kk": "Бұғаттан шығару", "en": "Unblock"},
    "btn_admin_user_tests": {"ru": "Тесты пользователя", "kk": "Қолданушы тесттері", "en": "User tests"},
    "btn_admin_full_test": {"ru": "Показать весь тест", "kk": "Толық тестті көрсету", "en": "Show full test"},
    "btn_admin_delete_test": {"ru": "Удалить тест", "kk": "Тестті өшіру", "en": "Delete test"},
    "btn_admin_delete_all": {"ru": "Удалить у всех", "kk": "Бәрінен өшіру", "en": "Delete for everyone"},
    "btn_admin_back": {"ru": "Назад в админку", "kk": "Әкімшіге оралу", "en": "Back to admin"},
    "admin_empty": {"ru": "Пока пусто.", "kk": "Әзірге бос.", "en": "Empty."},
    "admin_original": {"ru": "оригинал", "kk": "түпнұсқа", "en": "original"},
    "admin_copy": {"ru": "копия", "kk": "көшірме", "en": "copy"},
    "admin_test_preview_note": {
        "ru": "Показаны первые вопросы. Полный список можно отправить отдельными сообщениями.",
        "kk": "Алғашқы сұрақтар көрсетілді. Толық тізімді бөлек хабарламалармен жіберуге болады.",
        "en": "First questions are shown. The full list can be sent as separate messages.",
    },
}

router = Router()
database: Database
bot_username: str | None = None
active_attempts: dict[int, "AttemptSession"] = {}
active_timers: dict[int, asyncio.Task] = {}
admin_ids: set[int] = set()


@dataclass(frozen=True)
class RunningQuestion:
    id: int
    text: str
    answers: tuple[DbAnswer, ...]


@dataclass
class AttemptSession:
    user_id: int
    chat_id: int
    message_id: int
    test_id: int
    title: str
    mode: str
    started_at: str
    questions: list[RunningQuestion]
    deadline_ts: float | None = None
    current_index: int = 0
    choices: list[AttemptChoice] = field(default_factory=list)
    notice_html: str | None = None
    display_title: str | None = None

    @property
    def current_question(self) -> RunningQuestion:
        return self.questions[self.current_index]


class AdminFilter(BaseFilter):
    async def __call__(self, event: Message | CallbackQuery) -> bool:
        return event.from_user is not None and event.from_user.id in admin_ids


class PausedFilter(BaseFilter):
    async def __call__(self, event: CallbackQuery) -> bool:
        if event.from_user is None:
            return False
        lang = language_for(event.from_user.id)
        return bot_access_denial(event.from_user.id, lang) is not None


@router.message(Command("start", "menu"))
async def handle_start(message: Message) -> None:
    register_user(message.from_user)
    lang = language_for(message.from_user.id)
    denial = bot_access_denial(message.from_user.id, lang)
    if denial:
        await message.answer(denial)
        return
    payload = start_payload(message)
    if payload.startswith("s_"):
        await handle_shared_start(message, payload.removeprefix("s_"), lang)
        return

    await message.answer(
        home_text(lang),
        reply_markup=main_menu_keyboard(lang, message.from_user.id),
    )


@router.message(Command("language"))
async def handle_language(message: Message) -> None:
    register_user(message.from_user)
    lang = language_for(message.from_user.id)
    denial = bot_access_denial(message.from_user.id, lang)
    if denial:
        await message.answer(denial)
        return
    await message.answer(
        t(lang, "language_title"),
        reply_markup=language_keyboard(),
    )


@router.message(Command("myid"))
async def handle_myid(message: Message) -> None:
    register_user(message.from_user)
    await message.answer(f"Ваш Telegram ID: <code>{message.from_user.id}</code>")


@router.message(Command("admin"), AdminFilter())
async def handle_admin(message: Message) -> None:
    register_user(message.from_user)
    lang = language_for(message.from_user.id)
    await message.answer(admin_text(lang), reply_markup=admin_keyboard(lang))


@router.message(Command("admin"))
async def handle_admin_denied(message: Message) -> None:
    register_user(message.from_user)
    lang = language_for(message.from_user.id)
    await message.answer(t(lang, "admin_only"))


@router.message(Command("cancel"))
async def handle_cancel(message: Message) -> None:
    register_user(message.from_user)
    lang = language_for(message.from_user.id)
    denial = bot_access_denial(message.from_user.id, lang)
    if denial:
        await message.answer(denial)
        return
    session = active_attempts.pop(message.from_user.id, None)
    if session is None:
        await message.answer(t(lang, "active_none"), reply_markup=main_menu_keyboard(lang, message.from_user.id))
        return

    cancel_timer(message.from_user.id)
    await finish_stopped_attempt(message, session, lang)


@router.message(F.document)
async def handle_document(message: Message, bot: Bot) -> None:
    register_user(message.from_user)
    lang = language_for(message.from_user.id)
    denial = bot_access_denial(message.from_user.id, lang)
    if denial:
        await message.answer(denial)
        return
    document = message.document
    filename = document.file_name or "test.docx"
    extension = Path(filename).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        await message.answer(
            t(lang, "unsupported_format"),
            reply_markup=main_menu_keyboard(lang, message.from_user.id),
        )
        return

    if document.file_size and document.file_size > MAX_UPLOAD_BYTES:
        await message.answer(t(lang, "too_big"))
        return

    await message.answer(t(lang, "file_received"))

    try:
        telegram_file = await bot.get_file(document.file_id)
        buffer = BytesIO()
        await bot.download_file(telegram_file.file_path, destination=buffer)
        parsed_test = parse_test_file(filename, buffer.getvalue())
        test_id = database.create_test(
            owner_id=message.from_user.id,
            source_filename=filename,
            parsed_test=parsed_test,
        )
    except TestParseError as exc:
        await message.answer(t(lang, "parse_fail", error=h(str(exc))))
        return
    except Exception:
        logging.exception("Unexpected upload error")
        await message.answer(t(lang, "unexpected_upload"))
        return

    await message.answer(
        f"<b>{t(lang, 'test_added')}</b>\n\n"
        f"{t(lang, 'label_title')}: <b>{h(parsed_test.title)}</b>\n"
        f"{t(lang, 'label_questions')}: <b>{len(parsed_test.questions)}</b>",
        reply_markup=test_saved_keyboard(test_id, lang),
    )


@router.callback_query(F.data == "menu")
async def callback_menu(callback: CallbackQuery) -> None:
    register_user(callback.from_user)
    lang = language_for(callback.from_user.id)
    denial = bot_access_denial(callback.from_user.id, lang)
    if denial:
        await callback.answer(denial, show_alert=True)
        return
    await safe_edit_text(
        callback.message,
        home_text(lang),
        reply_markup=main_menu_keyboard(lang, callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data == "upload")
async def callback_upload(callback: CallbackQuery) -> None:
    register_user(callback.from_user)
    lang = language_for(callback.from_user.id)
    denial = bot_access_denial(callback.from_user.id, lang)
    if denial:
        await callback.answer(denial, show_alert=True)
        return
    await safe_edit_text(
        callback.message,
        t(lang, "upload_text"),
        reply_markup=back_to_menu_keyboard(lang),
    )
    await callback.answer()


@router.callback_query(F.data == "language")
async def callback_language(callback: CallbackQuery) -> None:
    register_user(callback.from_user)
    lang = language_for(callback.from_user.id)
    denial = bot_access_denial(callback.from_user.id, lang)
    if denial:
        await callback.answer(denial, show_alert=True)
        return
    await safe_edit_text(
        callback.message,
        t(lang, "language_title"),
        reply_markup=language_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("lang:set:"))
async def callback_set_language(callback: CallbackQuery) -> None:
    register_user(callback.from_user)
    lang = callback.data.rsplit(":", maxsplit=1)[1]
    current_lang = language_for(callback.from_user.id)
    denial = bot_access_denial(callback.from_user.id, current_lang)
    if denial:
        await callback.answer(denial, show_alert=True)
        return
    if lang not in LANGUAGES:
        lang = "ru"
    database.set_user_language(callback.from_user.id, lang)
    await safe_edit_text(
        callback.message,
        f"{t(lang, 'language_saved')}\n\n{home_text(lang)}",
        reply_markup=main_menu_keyboard(lang, callback.from_user.id),
    )
    await callback.answer(t(lang, "language_saved"))


@router.callback_query(F.data.startswith("admin:"), AdminFilter())
async def callback_admin(callback: CallbackQuery) -> None:
    register_user(callback.from_user)
    lang = language_for(callback.from_user.id)
    parts = callback.data.split(":")
    action = parts[1] if len(parts) > 1 else "menu"

    if action == "pause":
        database.set_setting("paused", "1")
        await safe_edit_text(callback.message, t(lang, "admin_paused"), reply_markup=admin_keyboard(lang))
    elif action == "resume":
        database.set_setting("paused", "0")
        await safe_edit_text(callback.message, t(lang, "admin_resumed"), reply_markup=admin_keyboard(lang))
    elif action == "stats":
        await safe_edit_text(callback.message, admin_stats_text(lang), reply_markup=admin_keyboard(lang))
    elif action == "tests":
        rows = database.admin_recent_tests()
        await safe_edit_text(
            callback.message,
            admin_recent_tests_text(lang, rows),
            reply_markup=admin_recent_tests_keyboard(rows, lang),
        )
    elif action == "attempts":
        await safe_edit_text(callback.message, admin_recent_attempts_text(lang), reply_markup=admin_keyboard(lang))
    elif action == "users":
        rows = database.admin_list_users()
        await safe_edit_text(
            callback.message,
            admin_users_text(rows, lang),
            reply_markup=admin_users_keyboard(rows, lang),
        )
    elif action == "user" and len(parts) >= 3:
        user_id = int(parts[2])
        await safe_edit_text(
            callback.message,
            admin_user_text(user_id, lang),
            reply_markup=admin_user_keyboard(user_id, lang),
        )
    elif action == "user_tests" and len(parts) >= 3:
        user_id = int(parts[2])
        rows = database.admin_user_tests(user_id)
        await safe_edit_text(
            callback.message,
            admin_user_tests_text(user_id, rows, lang),
            reply_markup=admin_user_tests_keyboard(user_id, rows, lang),
        )
    elif action in {"block", "unblock"} and len(parts) >= 3:
        user_id = int(parts[2])
        if user_id in admin_ids:
            await callback.answer(t(lang, "admin_cannot_block_admin"), show_alert=True)
            return
        blocked = action == "block"
        database.set_user_blocked(user_id, blocked)
        session = active_attempts.pop(user_id, None)
        if session is not None:
            cancel_timer(user_id)
        await safe_edit_text(
            callback.message,
            f"{t(lang, 'admin_blocked' if blocked else 'admin_unblocked')}\n\n"
            f"{admin_user_text(user_id, lang)}",
            reply_markup=admin_user_keyboard(user_id, lang),
        )
    elif action == "test" and len(parts) >= 4:
        test_id = int(parts[2])
        owner_id = int(parts[3])
        test = database.admin_get_test(test_id)
        if test is None:
            await callback.answer(t(lang, "not_found_test"), show_alert=True)
            return
        await safe_edit_text(
            callback.message,
            admin_test_preview_text(test, lang),
            reply_markup=admin_test_keyboard(test.id, owner_id, lang),
        )
    elif action == "test_full" and len(parts) >= 4:
        test_id = int(parts[2])
        owner_id = int(parts[3])
        test = database.admin_get_test(test_id)
        if test is None:
            await callback.answer(t(lang, "not_found_test"), show_alert=True)
            return
        chunks = build_admin_test_chunks(test, lang, limit=None)
        await safe_edit_text(
            callback.message,
            admin_test_preview_text(test, lang),
            reply_markup=admin_test_keyboard(test.id, owner_id, lang),
        )
        for chunk in chunks:
            await callback.message.answer(chunk)
    elif action == "test_delete" and len(parts) >= 4:
        test_id = int(parts[2])
        owner_id = int(parts[3])
        test = database.admin_get_test(test_id)
        if test is None:
            await callback.answer(t(lang, "not_found_test"), show_alert=True)
            return
        await safe_edit_text(
            callback.message,
            t(lang, "admin_delete_test_prompt", title=h(test.title)),
            reply_markup=admin_delete_test_confirm_keyboard(test_id, owner_id, lang),
        )
    elif action == "test_delete_all" and len(parts) >= 4:
        test_id = int(parts[2])
        owner_id = int(parts[3])
        test = database.admin_get_test(test_id)
        if test is None:
            await callback.answer(t(lang, "not_found_test"), show_alert=True)
            return
        await safe_edit_text(
            callback.message,
            t(lang, "admin_delete_all_prompt", title=h(test.title)),
            reply_markup=admin_delete_all_confirm_keyboard(test_id, owner_id, lang),
        )
    elif action == "test_delete_confirm" and len(parts) >= 4:
        test_id = int(parts[2])
        owner_id = int(parts[3])
        deleted = database.admin_delete_test(test_id)
        if not deleted:
            await callback.answer(t(lang, "not_found_test"), show_alert=True)
            return
        stop_attempts_for_tests([test_id])
        rows = database.admin_user_tests(owner_id)
        await safe_edit_text(
            callback.message,
            f"{t(lang, 'admin_test_deleted')}\n\n{admin_user_tests_text(owner_id, rows, lang)}",
            reply_markup=admin_user_tests_keyboard(owner_id, rows, lang),
        )
    elif action == "test_delete_all_confirm" and len(parts) >= 4:
        test_id = int(parts[2])
        owner_id = int(parts[3])
        deleted_ids = database.admin_delete_test_for_all(test_id)
        if not deleted_ids:
            await callback.answer(t(lang, "not_found_test"), show_alert=True)
            return
        stop_attempts_for_tests(deleted_ids)
        rows = database.admin_user_tests(owner_id)
        await safe_edit_text(
            callback.message,
            f"{t(lang, 'admin_test_deleted_all')}\n\n{admin_user_tests_text(owner_id, rows, lang)}",
            reply_markup=admin_user_tests_keyboard(owner_id, rows, lang),
        )
    elif action == "menu":
        await safe_edit_text(callback.message, admin_text(lang), reply_markup=admin_keyboard(lang))
    await callback.answer()


@router.callback_query(PausedFilter())
async def callback_paused(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    await callback.answer(bot_access_denial(callback.from_user.id, lang) or t(lang, "bot_paused"), show_alert=True)


@router.callback_query(F.data == "tests:list")
async def callback_tests(callback: CallbackQuery) -> None:
    await show_tests(callback, mode="view")


@router.callback_query(F.data == "tests:start_list")
async def callback_start_list(callback: CallbackQuery) -> None:
    await show_tests(callback, mode="start")


@router.callback_query(F.data.startswith("tests:view:"))
async def callback_view_test(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    test_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    test = database.get_test_summary(test_id, callback.from_user.id)
    if test is None:
        await callback.answer(t(lang, "not_found_test"), show_alert=True)
        return

    await safe_edit_text(
        callback.message,
        f"<b>{t(lang, 'test_card')}</b>\n\n"
        f"{t(lang, 'label_title')}: <b>{h(test['title'])}</b>\n"
        f"{t(lang, 'label_file')}: {h(test['source_filename'])}\n"
        f"{t(lang, 'label_questions')}: <b>{test['question_count']}</b>\n"
        f"{t(lang, 'label_added')}: {format_timestamp(test['created_at'])}",
        reply_markup=test_details_keyboard(
            test_id,
            lang,
            can_delete_all=test["source_test_id"] is None,
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tests:share:"))
async def callback_share_test(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    test_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    test = database.get_test_summary(test_id, callback.from_user.id)
    if test is None:
        await callback.answer(t(lang, "not_found_test"), show_alert=True)
        return

    token = database.get_or_create_share_token(test_id, callback.from_user.id)
    if token is None:
        await callback.answer(t(lang, "not_found_test"), show_alert=True)
        return

    link = shared_test_link(token)
    await safe_edit_text(
        callback.message,
        f"<b>{t(lang, 'share_created')}</b>\n\n"
        f"<a href=\"{h(link)}\">{h(link)}</a>\n\n"
        f"{t(lang, 'share_hint')}",
        reply_markup=share_keyboard(link, test_id, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tests:delete:"))
async def callback_delete_test(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    test_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    test = database.get_test_summary(test_id, callback.from_user.id)
    if test is None:
        await callback.answer(t(lang, "not_found_test"), show_alert=True)
        return

    await safe_edit_text(
        callback.message,
        t(lang, "delete_prompt", title=h(test["title"])),
        reply_markup=delete_confirm_keyboard(test_id, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tests:delete_all:"))
async def callback_delete_all_test(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    test_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    test = database.get_test_summary(test_id, callback.from_user.id)
    if test is None:
        await callback.answer(t(lang, "not_found_test"), show_alert=True)
        return
    if test["source_test_id"] is not None:
        await callback.answer(t(lang, "not_found_test"), show_alert=True)
        return

    await safe_edit_text(
        callback.message,
        t(lang, "delete_all_prompt", title=h(test["title"])),
        reply_markup=delete_all_confirm_keyboard(test_id, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tests:delete_confirm:"))
async def callback_delete_confirm(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    test_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    deleted = database.delete_test(test_id, callback.from_user.id)
    session = active_attempts.get(callback.from_user.id)
    if session and session.test_id == test_id:
        active_attempts.pop(callback.from_user.id, None)
        cancel_timer(callback.from_user.id)

    if not deleted:
        await callback.answer(t(lang, "already_deleted"), show_alert=True)
        return

    await safe_edit_text(
        callback.message,
        t(lang, "deleted"),
        reply_markup=main_menu_keyboard(lang, callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tests:delete_all_confirm:"))
async def callback_delete_all_confirm(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    test_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    deleted_ids = database.delete_test_for_all(test_id, callback.from_user.id)
    if not deleted_ids:
        await callback.answer(t(lang, "not_found_test"), show_alert=True)
        return

    for user_id, session in list(active_attempts.items()):
        if session.test_id in deleted_ids:
            active_attempts.pop(user_id, None)
            cancel_timer(user_id)

    await safe_edit_text(
        callback.message,
        t(lang, "deleted_all"),
        reply_markup=main_menu_keyboard(lang, callback.from_user.id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("tests:start:"))
async def callback_start_test(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    test_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    test = database.get_test_summary(test_id, callback.from_user.id)
    if test is None:
        await callback.answer(t(lang, "not_found_test"), show_alert=True)
        return

    await safe_edit_text(
        callback.message,
        f"<b>{h(test['title'])}</b>\n\n{t(lang, 'choose_mode')}",
        reply_markup=mode_keyboard(test_id, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("attempt:mode:"))
async def callback_choose_mode(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    _, _, test_id_raw, mode = callback.data.split(":")
    test_id = int(test_id_raw)
    if mode not in {MODE_TRAINING, MODE_MISTAKES}:
        await callback.answer(t(lang, "bad_answer"), show_alert=True)
        return

    await safe_edit_text(
        callback.message,
        f"<b>{mode_name(mode, lang)}</b>\n\n{t(lang, 'choose_size')}",
        reply_markup=size_keyboard(test_id, mode, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("attempt:size:"))
async def callback_choose_size(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    _, _, test_id_raw, mode, size = callback.data.split(":")
    test_id = int(test_id_raw)
    if mode not in {MODE_TRAINING, MODE_MISTAKES} or size not in QUESTION_SIZES:
        await callback.answer(t(lang, "bad_answer"), show_alert=True)
        return

    await safe_edit_text(
        callback.message,
        f"<b>{mode_name(mode, lang)}</b>\n\n{t(lang, 'choose_timer')}",
        reply_markup=timer_keyboard(test_id, mode, size, lang),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("attempt:timer:"))
async def callback_choose_timer(callback: CallbackQuery, bot: Bot) -> None:
    lang = language_for(callback.from_user.id)
    _, _, test_id_raw, mode, size, minutes_raw = callback.data.split(":")
    test_id = int(test_id_raw)
    minutes = int(minutes_raw)
    if mode not in {MODE_TRAINING, MODE_MISTAKES} or size not in QUESTION_SIZES:
        await callback.answer(t(lang, "bad_answer"), show_alert=True)
        return

    await start_configured_attempt(
        callback=callback,
        bot=bot,
        test_id=test_id,
        mode=mode,
        size=size,
        minutes=minutes,
        lang=lang,
    )


@router.callback_query(F.data.startswith("answer:"))
async def callback_answer(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    session = active_attempts.get(callback.from_user.id)
    if session is None:
        await callback.answer(t(lang, "inactive_attempt"), show_alert=True)
        return

    try:
        _, question_id_raw, answer_id_raw = callback.data.split(":")
        question_id = int(question_id_raw)
        selected_answer_id = int(answer_id_raw)
    except ValueError:
        await callback.answer(t(lang, "bad_answer"), show_alert=True)
        return

    question = session.current_question
    if question.id != question_id:
        await callback.answer(t(lang, "old_button"), show_alert=True)
        return

    selected_answer = next(
        (answer for answer in question.answers if answer.id == selected_answer_id),
        None,
    )
    correct_answer = next(answer for answer in question.answers if answer.is_correct)
    if selected_answer is None:
        await callback.answer(t(lang, "answer_missing"), show_alert=True)
        return

    is_correct = selected_answer.id == correct_answer.id
    session.choices.append(
        AttemptChoice(
            question_id=question.id,
            selected_answer_id=selected_answer.id,
            correct_answer_id=correct_answer.id,
            is_correct=is_correct,
        )
    )

    session.current_index += 1
    if session.current_index >= len(session.questions):
        active_attempts.pop(callback.from_user.id, None)
        cancel_timer(callback.from_user.id)
        text, reply_markup = finish_attempt_payload(session, lang, stopped=False, timed_out=False)
        await safe_edit_text(callback.message, text, reply_markup=reply_markup)
        await callback.answer(t(lang, "answer_saved"))
        return

    session.notice_html = feedback_text(lang, is_correct, selected_answer, correct_answer)
    session.display_title = t(lang, "next_question")
    await safe_edit_text(
        callback.message,
        render_current_question(session, lang),
        reply_markup=answer_keyboard(session.current_question, lang),
    )
    await callback.answer(t(lang, "answer_saved"))


@router.callback_query(F.data == "attempt:cancel")
async def callback_cancel_attempt(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    session = active_attempts.pop(callback.from_user.id, None)
    if session is None:
        await callback.answer(t(lang, "active_none"), show_alert=True)
        return

    cancel_timer(callback.from_user.id)
    await finish_stopped_attempt(callback, session, lang)
    await callback.answer()


@router.callback_query(F.data == "mistakes:list")
async def callback_mistakes_list(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    attempts = database.list_recent_attempts_with_mistakes(callback.from_user.id)
    if not attempts:
        await safe_edit_text(
            callback.message,
            t(lang, "no_mistakes"),
            reply_markup=main_menu_keyboard(lang, callback.from_user.id),
        )
        await callback.answer()
        return

    rows = []
    for attempt in attempts:
        wrong = int(attempt["total_count"]) - int(attempt["correct_count"])
        label = shorten(
            f"{attempt['title']} - {wrong} / {attempt['percent']:g}%",
            width=55,
            placeholder="...",
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"mistakes:view:{attempt['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="menu")])

    await safe_edit_text(
        callback.message,
        t(lang, "choose_attempt"),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("mistakes:view:"))
async def callback_mistakes_view(callback: CallbackQuery) -> None:
    lang = language_for(callback.from_user.id)
    attempt_id = int(callback.data.rsplit(":", maxsplit=1)[1])
    mistakes = database.get_attempt_mistakes(attempt_id, callback.from_user.id)
    if not mistakes:
        await safe_edit_text(
            callback.message,
            t(lang, "attempt_without_mistakes"),
            reply_markup=main_menu_keyboard(lang, callback.from_user.id),
        )
        await callback.answer()
        return

    chunks = build_mistake_chunks(mistakes, lang)
    await safe_edit_text(callback.message, f"<b>{t(lang, 'mistakes_header')}</b>")
    for chunk in chunks:
        await callback.message.answer(chunk)
    await callback.message.answer(t(lang, "mistakes_done"), reply_markup=main_menu_keyboard(lang, callback.from_user.id))
    await callback.answer()


async def show_tests(callback: CallbackQuery, mode: str) -> None:
    lang = language_for(callback.from_user.id)
    tests = database.list_tests(callback.from_user.id)
    if not tests:
        await safe_edit_text(
            callback.message,
            t(lang, "no_tests"),
            reply_markup=main_menu_keyboard(lang, callback.from_user.id),
        )
        await callback.answer()
        return

    rows = []
    for test in tests[:25]:
        label = shorten(
            f"{test['title']} ({test['question_count']})",
            width=55,
            placeholder="...",
        )
        callback_data = (
            f"tests:start:{test['id']}" if mode == "start" else f"tests:view:{test['id']}"
        )
        rows.append([InlineKeyboardButton(text=label, callback_data=callback_data)])

    rows.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="menu")])
    title = t(lang, "choose_test") if mode == "start" else t(lang, "saved_tests")
    await safe_edit_text(
        callback.message,
        title,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


async def handle_shared_start(message: Message, token: str, lang: str) -> None:
    summary = database.get_shared_test_summary(token)
    if summary is None:
        await message.answer(t(lang, "share_not_found"), reply_markup=main_menu_keyboard(lang, message.from_user.id))
        return

    copied_test_id = database.copy_shared_test_to_owner(token, message.from_user.id)
    if copied_test_id is None:
        await message.answer(t(lang, "share_not_found"), reply_markup=main_menu_keyboard(lang, message.from_user.id))
        return

    copied = database.get_test_summary(copied_test_id, message.from_user.id)
    title = copied["title"] if copied else summary["title"]
    question_count = copied["question_count"] if copied else summary["question_count"]
    await message.answer(
        f"<b>{t(lang, 'shared_imported')}</b>\n\n"
        f"{t(lang, 'label_title')}: <b>{h(title)}</b>\n"
        f"{t(lang, 'label_questions')}: <b>{question_count}</b>",
        reply_markup=test_saved_keyboard(copied_test_id, lang),
    )


async def start_configured_attempt(
    callback: CallbackQuery,
    bot: Bot,
    test_id: int,
    mode: str,
    size: str,
    minutes: int,
    lang: str,
) -> None:
    test = database.get_test_for_attempt(test_id, callback.from_user.id)
    if test is None:
        await callback.answer(t(lang, "not_found_test"), show_alert=True)
        return

    if mode == MODE_MISTAKES:
        questions = database.get_mistake_questions_for_test(test_id, callback.from_user.id)
        if not questions:
            await callback.answer(t(lang, "no_mistake_questions"), show_alert=True)
            return
    else:
        questions = list(test.questions)

    if not questions:
        await callback.answer(t(lang, "empty_test"), show_alert=True)
        return

    active_attempts.pop(callback.from_user.id, None)
    cancel_timer(callback.from_user.id)
    session = build_attempt_session(
        user_id=callback.from_user.id,
        chat_id=callback.message.chat.id,
        message_id=callback.message.message_id,
        test_id=test.id,
        title=test.title,
        questions=questions,
        mode=mode,
        size=size,
        duration_minutes=minutes,
    )
    active_attempts[callback.from_user.id] = session

    if session.deadline_ts is not None:
        active_timers[session.user_id] = asyncio.create_task(
            finish_when_timer_expires(bot, session.user_id, session.deadline_ts, lang)
        )

    await safe_edit_text(
        callback.message,
        render_current_question(session, lang),
        reply_markup=answer_keyboard(session.current_question, lang),
    )
    await callback.answer()


def build_attempt_session(
    user_id: int,
    chat_id: int,
    message_id: int,
    test_id: int,
    title: str,
    questions: list[DbQuestion],
    mode: str,
    size: str,
    duration_minutes: int,
) -> AttemptSession:
    shuffled_questions = questions[:]
    random.shuffle(shuffled_questions)
    limit = question_limit(size, len(shuffled_questions))
    shuffled_questions = shuffled_questions[:limit]

    running_questions: list[RunningQuestion] = []
    for question in shuffled_questions:
        answers = list(question.answers)
        random.shuffle(answers)
        running_questions.append(
            RunningQuestion(
                id=question.id,
                text=question.text,
                answers=tuple(answers),
            )
        )

    return AttemptSession(
        user_id=user_id,
        chat_id=chat_id,
        message_id=message_id,
        test_id=test_id,
        title=title,
        mode=mode,
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        questions=running_questions,
        deadline_ts=(time.time() + duration_minutes * 60 if duration_minutes else None),
    )


def render_current_question(session: AttemptSession, lang: str, prefix: str | None = None) -> str:
    question = session.current_question
    current_number = session.current_index + 1
    total = len(session.questions)
    title = prefix or session.display_title or session.title
    lines = [
        f"<b>{h(title)}</b>",
        f"{t(lang, 'mode_label')}: <b>{mode_name(session.mode, lang)}</b> | "
        f"{t(lang, 'label_question')}: <b>{current_number}</b> / <b>{total}</b>",
        f"{t(lang, 'progress_label')}: {progress_bar(session.current_index, total)}",
    ]
    if session.deadline_ts is not None:
        lines.append(f"{t(lang, 'timer_left')}: <b>{remaining_time_text(session.deadline_ts)}</b>")
    if session.notice_html:
        lines.extend(["", session.notice_html])
    lines.extend(
        [
            "",
            f"<b>{h(shorten(question.text, width=900, placeholder='...'))}</b>",
            "",
        ]
    )

    for index, answer in enumerate(question.answers):
        letter = option_label(index)
        answer_text = shorten(answer.text, width=350, placeholder="...")
        lines.append(f"<b>{letter}.</b> {h(answer_text)}")

    return trim_message("\n".join(lines))


def answer_keyboard(question: RunningQuestion, lang: str) -> InlineKeyboardMarkup:
    rows = []
    for index, answer in enumerate(question.answers):
        label = shorten(
            f"{option_label(index)}. {answer.text}",
            width=48,
            placeholder="...",
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"answer:{question.id}:{answer.id}",
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text=t(lang, "btn_stop_result"), callback_data="attempt:cancel")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def main_menu_keyboard(lang: str, user_id: int | None = None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t(lang, "btn_add_test"), callback_data="upload")],
        [InlineKeyboardButton(text=t(lang, "btn_my_tests"), callback_data="tests:list")],
        [InlineKeyboardButton(text=t(lang, "btn_language"), callback_data="language")],
    ]
    if user_id in admin_ids:
        rows.append([InlineKeyboardButton(text=t(lang, "admin_title"), callback_data="admin:menu")])
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def back_to_menu_keyboard(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="menu")]]
    )


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=LANGUAGE_NAMES["ru"], callback_data="lang:set:ru")],
            [InlineKeyboardButton(text=LANGUAGE_NAMES["kk"], callback_data="lang:set:kk")],
            [InlineKeyboardButton(text=LANGUAGE_NAMES["en"], callback_data="lang:set:en")],
        ]
    )


def test_saved_keyboard(test_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_start_attempt"), callback_data=f"tests:start:{test_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_my_tests"), callback_data="tests:list")],
            [InlineKeyboardButton(text=t(lang, "btn_main_menu"), callback_data="menu")],
        ]
    )


def test_details_keyboard(test_id: int, lang: str, can_delete_all: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t(lang, "btn_start_attempt"), callback_data=f"tests:start:{test_id}")],
        [InlineKeyboardButton(text=t(lang, "btn_share"), callback_data=f"tests:share:{test_id}")],
    ]
    if can_delete_all:
        rows.append([InlineKeyboardButton(text=t(lang, "btn_delete_all"), callback_data=f"tests:delete_all:{test_id}")])
    rows.extend(
        [
            [InlineKeyboardButton(text=t(lang, "btn_delete_self"), callback_data=f"tests:delete:{test_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data="tests:list")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def share_keyboard(link: str, test_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_copy_link"), url=link)],
            [InlineKeyboardButton(text=t(lang, "btn_start_attempt"), callback_data=f"tests:start:{test_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data=f"tests:view:{test_id}")],
        ]
    )


def mode_keyboard(test_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "mode_training"), callback_data=f"attempt:mode:{test_id}:{MODE_TRAINING}")],
            [InlineKeyboardButton(text=t(lang, "mode_mistakes"), callback_data=f"attempt:mode:{test_id}:{MODE_MISTAKES}")],
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data=f"tests:view:{test_id}")],
        ]
    )


def size_keyboard(test_id: int, mode: str, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="10", callback_data=f"attempt:size:{test_id}:{mode}:10"),
                InlineKeyboardButton(text="20", callback_data=f"attempt:size:{test_id}:{mode}:20"),
                InlineKeyboardButton(text="50", callback_data=f"attempt:size:{test_id}:{mode}:50"),
            ],
            [InlineKeyboardButton(text=t(lang, "btn_all_questions"), callback_data=f"attempt:size:{test_id}:{mode}:all")],
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data=f"tests:start:{test_id}")],
        ]
    )


def timer_keyboard(test_id: int, mode: str, size: str, lang: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=t(lang, "btn_no_timer"), callback_data=f"attempt:timer:{test_id}:{mode}:{size}:0")]
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(text="5 мин", callback_data=f"attempt:timer:{test_id}:{mode}:{size}:5"),
                InlineKeyboardButton(text="10 мин", callback_data=f"attempt:timer:{test_id}:{mode}:{size}:10"),
            ],
            [
                InlineKeyboardButton(text="20 мин", callback_data=f"attempt:timer:{test_id}:{mode}:{size}:20"),
                InlineKeyboardButton(text="30 мин", callback_data=f"attempt:timer:{test_id}:{mode}:{size}:30"),
            ],
            [InlineKeyboardButton(text="60 мин", callback_data=f"attempt:timer:{test_id}:{mode}:{size}:60")],
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data=f"attempt:mode:{test_id}:{mode}")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def delete_confirm_keyboard(test_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_confirm_delete"), callback_data=f"tests:delete_confirm:{test_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_cancel"), callback_data=f"tests:view:{test_id}")],
        ]
    )


def delete_all_confirm_keyboard(test_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_delete_all"), callback_data=f"tests:delete_all_confirm:{test_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_cancel"), callback_data=f"tests:view:{test_id}")],
        ]
    )


def result_keyboard(attempt_id: int, test_id: int, has_mistakes: bool, lang: str) -> InlineKeyboardMarkup:
    rows = []
    if has_mistakes:
        rows.append(
            [InlineKeyboardButton(text=t(lang, "btn_mistakes"), callback_data=f"mistakes:view:{attempt_id}")]
        )
    rows.extend(
        [
            [InlineKeyboardButton(text=t(lang, "btn_retry"), callback_data=f"tests:start:{test_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_my_tests"), callback_data="tests:list")],
            [InlineKeyboardButton(text=t(lang, "btn_main_menu"), callback_data="menu")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def finish_attempt_payload(
    session: AttemptSession,
    lang: str,
    stopped: bool,
    timed_out: bool,
) -> tuple[str, InlineKeyboardMarkup]:
    answered_count = len(session.choices)
    if answered_count == 0:
        text = result_text(
            lang=lang,
            title=session.title,
            mode=session.mode,
            correct_count=0,
            total_count=0,
            percent=0.0,
            answered_count=0,
            question_count=len(session.questions),
            stopped=stopped,
            timed_out=timed_out,
        )
        return text, stopped_without_answers_keyboard(session.test_id, lang)

    attempt_id, correct_count, total_count, percent = database.save_attempt(
        test_id=session.test_id,
        user_id=session.user_id,
        started_at=session.started_at,
        choices=session.choices,
    )
    text = result_text(
        lang=lang,
        title=session.title,
        mode=session.mode,
        correct_count=correct_count,
        total_count=total_count,
        percent=percent,
        answered_count=answered_count,
        question_count=len(session.questions),
        stopped=stopped,
        timed_out=timed_out,
    )
    return text, result_keyboard(
        attempt_id=attempt_id,
        test_id=session.test_id,
        has_mistakes=correct_count < total_count,
        lang=lang,
    )


async def finish_stopped_attempt(
    target: Message | CallbackQuery,
    session: AttemptSession,
    lang: str,
) -> None:
    text, reply_markup = finish_attempt_payload(session, lang, stopped=True, timed_out=False)

    if isinstance(target, CallbackQuery):
        await safe_edit_text(target.message, text, reply_markup=reply_markup)
    else:
        await target.answer(text, reply_markup=reply_markup)


async def finish_when_timer_expires(
    bot: Bot,
    user_id: int,
    deadline_ts: float,
    lang: str,
) -> None:
    while True:
        session = active_attempts.get(user_id)
        if session is None or session.deadline_ts != deadline_ts:
            return

        remaining = deadline_ts - time.time()
        if remaining <= 0:
            break

        await asyncio.sleep(min(TIMER_REFRESH_SECONDS, max(1.0, remaining)))
        session = active_attempts.get(user_id)
        if session is None or session.deadline_ts != deadline_ts:
            return
        if deadline_ts - time.time() > 0:
            try:
                await bot.edit_message_text(
                    render_current_question(session, lang),
                    chat_id=session.chat_id,
                    message_id=session.message_id,
                    reply_markup=answer_keyboard(session.current_question, lang),
                )
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc):
                    logging.warning("Timer message refresh failed: %s", exc)

    active_attempts.pop(user_id, None)
    active_timers.pop(user_id, None)
    text, reply_markup = finish_attempt_payload(session, lang, stopped=False, timed_out=True)
    try:
        await bot.edit_message_text(
            text,
            chat_id=session.chat_id,
            message_id=session.message_id,
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        logging.warning("Timer result edit failed: %s", exc)
        await bot.send_message(session.chat_id, text, reply_markup=reply_markup)


def cancel_timer(user_id: int) -> None:
    task = active_timers.pop(user_id, None)
    if task and not task.done():
        task.cancel()


def stop_attempts_for_tests(test_ids: list[int]) -> None:
    ids = set(test_ids)
    for user_id, session in list(active_attempts.items()):
        if session.test_id in ids:
            active_attempts.pop(user_id, None)
            cancel_timer(user_id)


def stopped_without_answers_keyboard(test_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_restart"), callback_data=f"tests:start:{test_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_main_menu"), callback_data="menu")],
        ]
    )


def admin_keyboard(lang: str) -> InlineKeyboardMarkup:
    paused = database.get_setting("paused", "0") == "1"
    toggle_text = t(lang, "btn_admin_resume") if paused else t(lang, "btn_admin_pause")
    toggle_action = "resume" if paused else "pause"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=toggle_text, callback_data=f"admin:{toggle_action}")],
            [InlineKeyboardButton(text=t(lang, "btn_admin_users"), callback_data="admin:users")],
            [
                InlineKeyboardButton(text=t(lang, "btn_admin_stats"), callback_data="admin:stats"),
                InlineKeyboardButton(text=t(lang, "btn_admin_tests"), callback_data="admin:tests"),
            ],
            [InlineKeyboardButton(text=t(lang, "btn_admin_attempts"), callback_data="admin:attempts")],
            [InlineKeyboardButton(text=t(lang, "btn_main_menu"), callback_data="menu")],
        ]
    )


def admin_users_keyboard(rows: list, lang: str) -> InlineKeyboardMarkup:
    keyboard_rows = []
    for row in rows:
        status = "blocked" if row["is_blocked"] else "active"
        label = shorten(
            f"{plain_user_name(row)} | {row['test_count']} tests | {status}",
            width=56,
            placeholder="...",
        )
        keyboard_rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"admin:user:{row['user_id']}")]
        )
    keyboard_rows.append([InlineKeyboardButton(text=t(lang, "btn_admin_back"), callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def admin_user_keyboard(user_id: int, lang: str) -> InlineKeyboardMarkup:
    user = database.admin_get_user(user_id)
    blocked = bool(user["is_blocked"]) if user else False
    toggle_key = "btn_admin_unblock" if blocked else "btn_admin_block"
    toggle_action = "unblock" if blocked else "block"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_admin_user_tests"), callback_data=f"admin:user_tests:{user_id}")],
            [InlineKeyboardButton(text=t(lang, toggle_key), callback_data=f"admin:{toggle_action}:{user_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_admin_users"), callback_data="admin:users")],
            [InlineKeyboardButton(text=t(lang, "btn_admin_back"), callback_data="admin:menu")],
        ]
    )


def admin_user_tests_keyboard(user_id: int, rows: list, lang: str) -> InlineKeyboardMarkup:
    keyboard_rows = []
    for row in rows:
        label = shorten(
            f"#{row['id']} {row['title']} ({row['question_count']})",
            width=56,
            placeholder="...",
        )
        keyboard_rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"admin:test:{row['id']}:{user_id}")]
        )
    keyboard_rows.append([InlineKeyboardButton(text=t(lang, "btn_back"), callback_data=f"admin:user:{user_id}")])
    keyboard_rows.append([InlineKeyboardButton(text=t(lang, "btn_admin_back"), callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def admin_recent_tests_keyboard(rows: list, lang: str) -> InlineKeyboardMarkup:
    keyboard_rows = []
    for row in rows:
        label = shorten(
            f"#{row['id']} {row['title']} ({row['question_count']})",
            width=56,
            placeholder="...",
        )
        keyboard_rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"admin:test:{row['id']}:{row['user_id']}")]
        )
    keyboard_rows.append([InlineKeyboardButton(text=t(lang, "btn_admin_back"), callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


def admin_test_keyboard(test_id: int, owner_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_admin_full_test"), callback_data=f"admin:test_full:{test_id}:{owner_id}")],
            [
                InlineKeyboardButton(text=t(lang, "btn_admin_delete_test"), callback_data=f"admin:test_delete:{test_id}:{owner_id}"),
                InlineKeyboardButton(text=t(lang, "btn_admin_delete_all"), callback_data=f"admin:test_delete_all:{test_id}:{owner_id}"),
            ],
            [InlineKeyboardButton(text=t(lang, "btn_admin_user_tests"), callback_data=f"admin:user_tests:{owner_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_back"), callback_data=f"admin:user:{owner_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_admin_back"), callback_data="admin:menu")],
        ]
    )


def admin_delete_test_confirm_keyboard(test_id: int, owner_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_admin_delete_test"), callback_data=f"admin:test_delete_confirm:{test_id}:{owner_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_cancel"), callback_data=f"admin:test:{test_id}:{owner_id}")],
        ]
    )


def admin_delete_all_confirm_keyboard(test_id: int, owner_id: int, lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=t(lang, "btn_admin_delete_all"), callback_data=f"admin:test_delete_all_confirm:{test_id}:{owner_id}")],
            [InlineKeyboardButton(text=t(lang, "btn_cancel"), callback_data=f"admin:test:{test_id}:{owner_id}")],
        ]
    )


async def safe_edit_text(message: Message, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc):
            raise


def language_for(user_id: int) -> str:
    lang = database.get_user_language(user_id)
    return lang if lang in LANGUAGES else "ru"


def register_user(user) -> None:
    if user is None:
        return
    database.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )


def bot_is_blocked(user_id: int) -> bool:
    return user_id not in admin_ids and database.is_user_blocked(user_id)


def bot_is_paused(user_id: int) -> bool:
    return user_id not in admin_ids and database.get_setting("paused", "0") == "1"


def bot_access_denial(user_id: int, lang: str) -> str | None:
    if bot_is_blocked(user_id):
        return t(lang, "user_blocked")
    if bot_is_paused(user_id):
        return t(lang, "bot_paused")
    return None


def start_payload(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def shared_test_link(token: str) -> str:
    username = bot_username or "GenTest6_bot"
    return f"https://t.me/{username}?start=s_{token}"


def admin_text(lang: str) -> str:
    status = t(lang, "admin_status_off") if database.get_setting("paused", "0") == "1" else t(lang, "admin_status_on")
    return f"<b>{t(lang, 'admin_title')}</b>\n\n{status}"


def admin_stats_text(lang: str) -> str:
    stats = database.admin_stats()
    status = t(lang, "admin_status_off") if database.get_setting("paused", "0") == "1" else t(lang, "admin_status_on")
    return (
        f"<b>{t(lang, 'btn_admin_stats')}</b>\n\n"
        f"{status}\n"
        f"{t(lang, 'label_users')}: <b>{stats['users']}</b>\n"
        f"{t(lang, 'label_blocked_users')}: <b>{stats['blocked_users']}</b>\n"
        f"{t(lang, 'label_tests')}: <b>{stats['tests']}</b>\n"
        f"{t(lang, 'label_attempts')}: <b>{stats['attempts']}</b>\n"
        f"{t(lang, 'label_share_links')}: <b>{stats['active_shares']}</b>"
    )


def admin_recent_tests_text(lang: str, rows: list | None = None) -> str:
    rows = database.admin_recent_tests() if rows is None else rows
    if not rows:
        return f"<b>{t(lang, 'btn_admin_tests')}</b>\n\n{t(lang, 'admin_empty')}"
    lines = [f"<b>{t(lang, 'btn_admin_tests')}</b>", ""]
    for row in rows:
        owner = format_user_row(row)
        copy_label = t(lang, "admin_copy") if row["source_test_id"] is not None else t(lang, "admin_original")
        lines.append(
            f"#{row['id']} {h(row['title'])} ({row['question_count']})\n"
            f"{owner} | {copy_label}\n"
            f"{t(lang, 'label_file')}: {h(row['source_filename'])}"
        )
    return "\n\n".join(lines)


def admin_recent_attempts_text(lang: str) -> str:
    rows = database.admin_recent_attempts()
    if not rows:
        return f"<b>{t(lang, 'btn_admin_attempts')}</b>\n\n{t(lang, 'admin_empty')}"
    lines = [f"<b>{t(lang, 'btn_admin_attempts')}</b>", ""]
    for row in rows:
        user = format_user_row(row)
        lines.append(
            f"#{row['id']} {h(row['title'])}\n"
            f"{user} | {row['correct_count']}/{row['total_count']} | {row['percent']:g}%"
        )
    return "\n\n".join(lines)


def admin_users_text(rows: list, lang: str) -> str:
    if not rows:
        return f"<b>{t(lang, 'btn_admin_users')}</b>\n\n{t(lang, 'admin_empty')}"
    lines = [f"<b>{t(lang, 'btn_admin_users')}</b>", ""]
    for row in rows[:10]:
        status = t(lang, "admin_user_blocked") if row["is_blocked"] else t(lang, "admin_user_active")
        lines.append(
            f"{format_user_row(row)}\n"
            f"{status} | {t(lang, 'label_tests')}: {row['test_count']} | "
            f"{t(lang, 'label_attempts')}: {row['attempt_count']}"
        )
    return "\n\n".join(lines)


def admin_user_text(user_id: int, lang: str) -> str:
    row = database.admin_get_user(user_id)
    if row is None:
        return f"<b>{t(lang, 'btn_admin_users')}</b>\n\n{t(lang, 'admin_empty')}"
    status = t(lang, "admin_user_blocked") if row["is_blocked"] else t(lang, "admin_user_active")
    full_name = " ".join(part for part in (row["first_name"], row["last_name"]) if part) or "-"
    username = f"@{row['username']}" if row["username"] else "-"
    return (
        f"<b>{t(lang, 'btn_admin_users')}</b>\n\n"
        f"ID: <code>{row['user_id']}</code>\n"
        f"Username: {h(username)}\n"
        f"Name: {h(full_name)}\n"
        f"Status: <b>{status}</b>\n"
        f"{t(lang, 'label_tests')}: <b>{row['test_count']}</b>\n"
        f"{t(lang, 'label_attempts')}: <b>{row['attempt_count']}</b>\n"
        f"Avg: <b>{row['avg_percent']:g}%</b>\n"
        f"Last seen: {format_timestamp(row['last_seen_at'])}"
    )


def admin_user_tests_text(user_id: int, rows: list, lang: str) -> str:
    user = database.admin_get_user(user_id)
    title = plain_user_name(user) if user else str(user_id)
    if not rows:
        return f"<b>{h(title)}</b>\n\n{t(lang, 'admin_empty')}"
    lines = [f"<b>{h(title)}</b>", ""]
    for row in rows:
        copy_label = t(lang, "admin_copy") if row["source_test_id"] is not None else t(lang, "admin_original")
        lines.append(
            f"#{row['id']} <b>{h(row['title'])}</b>\n"
            f"{t(lang, 'label_file')}: {h(row['source_filename'])}\n"
            f"{t(lang, 'label_questions')}: {row['question_count']} | {copy_label}\n"
            f"{t(lang, 'label_attempts')}: {row['attempt_count']} | Avg: {row['avg_percent']:g}%"
        )
    return trim_message("\n\n".join(lines))


def admin_test_preview_text(test, lang: str) -> str:
    lines = [
        f"<b>{t(lang, 'test_card')}</b>",
        "",
        f"ID: <code>{test.id}</code>",
        f"{t(lang, 'label_title')}: <b>{h(test.title)}</b>",
        f"{t(lang, 'label_file')}: {h(test.source_filename)}",
        f"{t(lang, 'label_questions')}: <b>{test.question_count}</b>",
        f"{t(lang, 'label_added')}: {format_timestamp(test.created_at)}",
        "",
        t(lang, "admin_test_preview_note"),
        "",
    ]
    lines.extend(build_admin_test_chunks(test, lang, limit=7, include_header=False))
    return trim_message("\n".join(lines))


def build_admin_test_chunks(test, lang: str, limit: int | None = None, include_header: bool = True) -> list[str]:
    chunks: list[str] = []
    current = (
        f"<b>{h(test.title)}</b>\n"
        f"{t(lang, 'label_questions')}: {test.question_count}\n\n"
        if include_header
        else ""
    )
    questions = test.questions if limit is None else test.questions[:limit]
    for question_index, question in enumerate(questions, start=1):
        block_lines = [f"<b>{question_index}. {h(question.text)}</b>"]
        for answer_index, answer in enumerate(question.answers, start=1):
            marker = f"{t(lang, 'correct_answer')}: " if answer.is_correct else ""
            block_lines.append(f"{answer_index}) {marker}{h(answer.text)}")
        block = "\n".join(block_lines) + "\n\n"
        if len(current) + len(block) > 3500 and current.strip():
            chunks.append(current.strip())
            current = ""
        current += block
    if current.strip():
        chunks.append(current.strip())
    return chunks


def format_user_row(row) -> str:
    username = row["username"]
    first_name = row["first_name"]
    last_name = row["last_name"]
    full_name = " ".join(part for part in (first_name, last_name) if part)
    if username:
        return f"@{h(username)} ({row['user_id']})"
    if full_name:
        return f"{h(full_name)} ({row['user_id']})"
    return str(row["user_id"])


def plain_user_name(row) -> str:
    if row is None:
        return "-"
    if row["username"]:
        return f"@{row['username']}"
    full_name = " ".join(part for part in (row["first_name"], row["last_name"]) if part)
    return full_name or str(row["user_id"])


def t(lang: str, key: str, **kwargs: object) -> str:
    values = TEXT[key]
    template = values.get(lang) or values["ru"]
    return template.format(**kwargs) if kwargs else template


def h(value: object) -> str:
    return escape(str(value), quote=False)


def progress_bar(value: int, total: int, width: int = 10) -> str:
    if total <= 0:
        return "[----------] 0%"
    ratio = max(0.0, min(1.0, value / total))
    filled = round(ratio * width)
    empty = width - filled
    percent = round(ratio * 100)
    return f"[{'■' * filled}{'□' * empty}] {percent}%"


def question_limit(size: str, total: int) -> int:
    if size == "all":
        return total
    return min(int(size), total)


def mode_name(mode: str, lang: str) -> str:
    if mode == MODE_MISTAKES:
        return t(lang, "mode_mistakes")
    return t(lang, "mode_training")


def remaining_time_text(deadline_ts: float) -> str:
    remaining = max(0, int(deadline_ts - time.time() + 0.999))
    minutes = remaining // 60
    seconds = remaining % 60
    return f"{minutes:02d}:{seconds:02d}"


def result_grade(percent: float, lang: str) -> str:
    if percent >= 90:
        return t(lang, "grade_excellent")
    if percent >= 70:
        return t(lang, "grade_good")
    if percent >= 50:
        return t(lang, "grade_repeat")
    return t(lang, "grade_weak")


def feedback_text(
    lang: str,
    is_correct: bool,
    selected_answer: DbAnswer,
    correct_answer: DbAnswer,
) -> str:
    if is_correct:
        return (
            f"<b>{t(lang, 'correct_feedback')}</b>\n"
            f"{t(lang, 'your_answer')}: <b>{h(selected_answer.text)}</b>"
        )

    return (
        f"<b>{t(lang, 'wrong_feedback')}</b>\n"
        f"{t(lang, 'your_answer')}: {h(selected_answer.text)}\n"
        f"{t(lang, 'correct_answer')}: <b>{h(correct_answer.text)}</b>"
    )


def home_text(lang: str) -> str:
    return (
        f"<b>{t(lang, 'home_title')}</b>\n\n"
        f"{t(lang, 'home_desc')}"
    )


def result_text(
    lang: str,
    title: str,
    mode: str,
    correct_count: int,
    total_count: int,
    percent: float,
    answered_count: int,
    question_count: int,
    stopped: bool,
    timed_out: bool = False,
) -> str:
    if timed_out:
        heading = t(lang, "attempt_timed_out")
    elif stopped:
        heading = t(lang, "attempt_stopped")
    else:
        heading = t(lang, "attempt_finished")
    lines = [
        f"<b>{heading}</b>",
        "",
        f"{t(lang, 'label_test')}: <b>{h(title)}</b>",
        f"{t(lang, 'mode_label')}: <b>{mode_name(mode, lang)}</b>",
        f"{t(lang, 'label_answered')}: <b>{answered_count}</b> / <b>{question_count}</b>",
    ]

    if total_count:
        lines.extend(
            [
                "",
                f"{t(lang, 'label_correct')}: <b>{correct_count}</b> / <b>{total_count}</b>",
                f"{t(lang, 'label_percent')}: <b>{percent:g}%</b>",
                result_grade(percent, lang),
            ]
        )
    else:
        lines.extend(
            [
                "",
                f"{t(lang, 'label_correct')}: <b>0</b>",
                f"{t(lang, 'label_percent')}: <b>0%</b>",
                "",
                t(lang, "not_saved_no_answers"),
            ]
        )

    if (stopped or timed_out) and total_count:
        lines.extend(["", t(lang, "stopped_note")])

    return "\n".join(lines)


def build_mistake_chunks(mistakes: list, lang: str) -> list[str]:
    chunks: list[str] = []
    current = ""

    for index, mistake in enumerate(mistakes, start=1):
        block = (
            f"<b>{index}. {h(mistake['question_text'])}</b>\n"
            f"{t(lang, 'your_answer')}: {h(mistake['selected_answer'])}\n"
            f"{t(lang, 'correct_answer')}: <b>{h(mistake['correct_answer'])}</b>\n\n"
        )
        if len(current) + len(block) > 3500 and current:
            chunks.append(current.strip())
            current = ""
        current += block

    if current:
        chunks.append(current.strip())
    return chunks


def trim_message(text: str) -> str:
    if len(text) <= 3900:
        return text
    return text[:3890].rstrip() + "\n..."


def format_timestamp(value: str) -> str:
    return value.replace("T", " ").replace("+00:00", " UTC")


def option_label(index: int) -> str:
    return LETTERS[index] if index < len(LETTERS) else str(index + 1)


def parse_admin_ids(value: str) -> set[int]:
    result: set[int] = set()
    for part in value.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit():
            result.add(int(part))
    return result


async def run_bot() -> None:
    global admin_ids, bot_username, database

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    token = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Set BOT_TOKEN or TELEGRAM_BOT_TOKEN in environment.")
    normalized_token = token.lower()
    if not TOKEN_RE.match(token) or any(part in normalized_token for part in PLACEHOLDER_TOKEN_PARTS):
        raise RuntimeError("BOT_TOKEN is invalid.")

    db_location = os.getenv("DATABASE_URL") or os.getenv("EXAM_BOT_DB_PATH", "data/exam_bot.sqlite3")
    database = Database(db_location)
    database.initialize()
    admin_ids = parse_admin_ids(os.getenv("BOT_ADMIN_IDS", ""))

    logging.basicConfig(level=logging.INFO)
    bot = Bot(
        token=token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError as exc:
        await bot.session.close()
        raise RuntimeError("Telegram rejected BOT_TOKEN.") from exc

    logging.info("Bot authorized as @%s", me.username)
    bot_username = me.username
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    port = os.getenv("PORT")
    external_url = os.getenv("WEBHOOK_URL") or os.getenv("RENDER_EXTERNAL_URL")
    if port and external_url:
        await run_webhook(bot, dispatcher, token, external_url, int(port))
        return

    await bot.delete_webhook(drop_pending_updates=False)
    health_runner = await start_health_server(port)
    try:
        await dispatcher.start_polling(bot)
    finally:
        if health_runner is not None:
            await health_runner.cleanup()
        await bot.session.close()


async def run_webhook(bot: Bot, dispatcher: Dispatcher, token: str, external_url: str, port: int) -> None:
    webhook_path = "/webhook/" + sha256(token.encode()).hexdigest()[:32]
    webhook_url = external_url.rstrip("/") + webhook_path
    secret_token = os.getenv("WEBHOOK_SECRET") or sha256(("webhook:" + token).encode()).hexdigest()

    app = web.Application()
    app.router.add_get("/", health_response)
    app.router.add_get("/health", health_response)
    SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        secret_token=secret_token,
    ).register(app, path=webhook_path)
    setup_application(app, dispatcher, bot=bot)

    await bot.set_webhook(
        webhook_url,
        secret_token=secret_token,
        drop_pending_updates=False,
    )

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info("Webhook server started on port %s", port)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await bot.session.close()


async def health_response(_: web.Request) -> web.Response:
    return web.Response(text="ok")


async def start_health_server(port: str | None = None) -> web.AppRunner | None:
    if not port:
        return None

    app = web.Application()
    app.router.add_get("/", health_response)
    app.router.add_get("/health", health_response)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(port))
    await site.start()
    logging.info("Health server started on port %s", port)
    return runner


def main() -> None:
    try:
        asyncio.run(run_bot())
    except RuntimeError as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
