"""Юнит-тесты маппинга ответа LanguageTool в наш формат ошибки.

См. infrastructure/languagetool.py и docs/08-references.md (LanguageTool
как офлайн-первый-проход перед LLM).
"""

from __future__ import annotations

from fire_safety_backend.infrastructure import languagetool


def test_match_to_error_maps_typo_category() -> None:
    match = {
        "message": "Возможно, опечатка",
        "replacements": [{"value": "правильно"}],
        "rule": {"category": {"id": "TYPOS"}},
        "context": {"text": "Эта неправльно написано.", "offset": 4, "length": 10},
    }
    error = languagetool._match_to_error(match)
    assert error["type"] == "орфография"
    assert error["before"] == "неправльно"
    assert error["after"] == "правильно"
    assert error["reason"] == "Возможно, опечатка"
    assert error["source"] == "languagetool"


def test_match_to_error_maps_punctuation_category() -> None:
    match = {
        "message": "Пропущена запятая",
        "replacements": [],
        "rule": {"category": {"id": "PUNCTUATION"}},
        "context": {"text": "Привет как дела", "offset": 0, "length": 6},
    }
    error = languagetool._match_to_error(match)
    assert error["type"] == "пунктуация"
    assert error["after"] == ""  # нет вариантов замены


def test_match_to_error_unknown_category_falls_back_to_style() -> None:
    match = {
        "message": "тест",
        "rule": {"category": {"id": "SOMETHING_NEW"}},
        "context": {"text": "текст", "offset": 0, "length": 5},
    }
    error = languagetool._match_to_error(match)
    assert error["type"] == "стиль"


async def test_check_returns_empty_list_when_server_unreachable() -> None:
    # На порту 8081 (или что в LANGUAGETOOL_HOST) в тестах ничего не поднято —
    # клиент должен деградировать тихо, а не бросать исключение.
    errors = await languagetool.check("Тестовый текст с ошибкой.")
    assert errors == []


async def test_check_empty_text_short_circuits() -> None:
    errors = await languagetool.check("   ")
    assert errors == []
