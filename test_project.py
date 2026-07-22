#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Тесты для project.py (pytest).

Запуск:
    pytest test_project.py

Тестируются чистые функции верхнего уровня из project.py. Они не зависят
от GUI, сети или внешних сервисов, поэтому проверяются детерминированно.
"""

import project
from project import (
    clean_message,
    parse_keywords,
    matches_topic_filter,
    make_post_id,
    is_valid_api_key,
    select_tone_instruction,
)


def test_clean_message():
    # Известные эмодзи заменяются на текстовые метки.
    assert clean_message("Старт 🚀") == "Старт [START]"
    assert clean_message("✅ готово ❌ ошибка") == "[OK] готово [ERROR] ошибка"
    # Несколько разных эмодзи в одной строке.
    assert clean_message("🤖💬") == "[BOT][MSG]"
    # Строка без эмодзи не меняется.
    assert clean_message("обычный текст") == "обычный текст"
    # Пустая строка остаётся пустой.
    assert clean_message("") == ""


def test_parse_keywords():
    # Разделение по запятой, нижний регистр, обрезка пробелов.
    assert parse_keywords("Python, AI, Крипта") == ["python", "ai", "крипта"]
    # Пустые элементы и лишние пробелы отбрасываются.
    assert parse_keywords("  Python ,, , AI ") == ["python", "ai"]
    # Пустая строка даёт пустой список.
    assert parse_keywords("") == []
    # Один элемент без запятых.
    assert parse_keywords("Python") == ["python"]


def test_matches_topic_filter():
    # Пустой список ключевых слов = фильтр выключен -> всегда True.
    assert matches_topic_filter("любой текст", []) is True
    # Совпадение без учёта регистра.
    assert matches_topic_filter("Я учу PYTHON", ["python"]) is True
    # Нет совпадений -> False.
    assert matches_topic_filter("текст про котов", ["python"]) is False
    # Требуется минимум 2 совпадения.
    assert matches_topic_filter("python и ai", ["python", "ai"], 2) is True
    assert matches_topic_filter("только python", ["python", "ai"], 2) is False


def test_make_post_id():
    # Идентификатор детерминирован для одних и тех же входных данных.
    assert make_post_id("user", "hello") == make_post_id("user", "hello")
    # Разный автор или текст -> разный идентификатор.
    assert make_post_id("user1", "hello") != make_post_id("user2", "hello")
    assert make_post_id("user", "hello") != make_post_id("user", "world")
    # Это шестнадцатеричный SHA-256 длиной 64 символа.
    post_id = make_post_id("автор", "текст поста")
    assert len(post_id) == 64
    assert all(c in "0123456789abcdef" for c in post_id)


def test_is_valid_api_key():
    # Валидный ключ начинается с 'sk-'.
    assert is_valid_api_key("sk-1234567890") is True
    # Невалидные варианты.
    assert is_valid_api_key("bad-key") is False
    assert is_valid_api_key("") is False
    assert is_valid_api_key(None) is False


def test_select_tone_instruction():
    # Известные тона возвращают свою инструкцию.
    assert select_tone_instruction("friendly") == project.TONE_INSTRUCTIONS["friendly"]
    assert select_tone_instruction("professional") == project.TONE_INSTRUCTIONS["professional"]
    # Неизвестный тон -> нейтральная инструкция по умолчанию.
    assert select_tone_instruction("нет такого тона") == project.TONE_INSTRUCTIONS["neutral"]


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
