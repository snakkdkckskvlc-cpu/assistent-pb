"""Юнит-тесты маппинга ответа LanguageTool в наш формат ошибки.

См. infrastructure/languagetool.py и docs/08-references.md (LanguageTool
как офлайн-первый-проход перед LLM).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
from fire_safety_backend.infrastructure import languagetool

if TYPE_CHECKING:
    import pytest


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


def test_match_to_error_empty_context_slice_yields_empty_before() -> None:
    # Раньше пустой срез схлопывался в фолбэк `or ctx_text`, подставляя
    # весь контекст-сниппет вместо честной пустой строки.
    match = {
        "message": "тест",
        "rule": {"category": {"id": "TYPOS"}},
        "context": {"text": "какой-то контекст целиком", "offset": 0, "length": 0},
    }
    error = languagetool._match_to_error(match)
    assert error["before"] == ""


class _FailingClient:
    async def post(self, *args, **kwargs):
        raise httpx.ConnectError("connection refused")

    async def get(self, *args, **kwargs):
        raise httpx.ConnectError("connection refused")


async def test_check_returns_empty_list_when_server_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Мокаем HTTP-слой напрямую — раньше тест реально стучался на
    # LANGUAGETOOL_HOST (тот же порт, что и настоящий sidecar) и мог
    # зависать на реальном таймауте, если sidecar был запущен вручную.
    monkeypatch.setattr(languagetool, "_get_client", lambda: _FailingClient())
    errors = await languagetool.check("Тестовый текст с ошибкой.")
    assert errors == []


async def test_check_empty_text_short_circuits() -> None:
    errors = await languagetool.check("   ")
    assert errors == []


class _MatchesResponse:
    def __init__(self, matches: list[dict]) -> None:
        self._matches = matches

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"matches": self._matches}


class _MatchesClient:
    def __init__(self, matches: list[dict]) -> None:
        self._matches = matches

    async def post(self, *args, **kwargs) -> _MatchesResponse:
        return _MatchesResponse(self._matches)


async def test_check_drops_style_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    # Проверка сужена до орфографии/пунктуации (см. languagetool.py::
    # _CATEGORY_TO_TYPE) — STYLE/LOGIC/EXTEND должны быть отброшены целиком, а
    # не помечены как "стиль".
    matches = [
        {
            "message": "опечатка",
            "rule": {"category": {"id": "TYPOS"}},
            "context": {"text": "текст с ошибкой", "offset": 0, "length": 5},
        },
        {
            "message": "пунктуация",
            "rule": {"category": {"id": "PUNCTUATION"}},
            "context": {"text": "текст без запятой", "offset": 0, "length": 5},
        },
        {
            "message": "стиль",
            "rule": {"category": {"id": "STYLE"}},
            "context": {"text": "канцелярский оборот", "offset": 0, "length": 5},
        },
        {
            "message": "многословие",
            "rule": {"category": {"id": "REDUNDANCY"}},
            "context": {"text": "масло масляное", "offset": 0, "length": 5},
        },
    ]
    monkeypatch.setattr(languagetool, "_get_client", lambda: _MatchesClient(matches))
    errors = await languagetool.check("текст с ошибкой без запятой канцелярский оборот")
    types = {e["type"] for e in errors}
    assert types == {"орфография", "пунктуация"}
    assert len(errors) == 2


async def test_check_keeps_grammar_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    """GRAMMAR отдаётся как орфография, и это замер, а не вкус.

    Пока категорию отбрасывали, вместе с ней терялось «в течении месяца»
    вместо «в течение» — обычная ошибка делового письма, которую не находил
    больше НИКТО: модель её тоже пропускает. Шума эта категория почти не даёт
    (на пяти настоящих договорах — одно срабатывание на весь набор)."""
    matches = [
        {
            "message": "предлог «в течение»",
            "rule": {"category": {"id": "GRAMMAR"}},
            "context": {"text": "В течении месяца работы", "offset": 0, "length": 16},
        },
    ]
    monkeypatch.setattr(languagetool, "_get_client", lambda: _MatchesClient(matches))
    errors = await languagetool.check("В течении месяца работы не начались")
    assert [e["type"] for e in errors] == ["орфография"]


async def test_healthcheck_returns_false_when_server_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(languagetool, "_get_client", lambda: _FailingClient())
    result = await languagetool.healthcheck()
    assert result == {"ok": False}


def test_check_filters_capitalized_typo_mid_sentence() -> None:
    # "ООО Монтажсвязьстрой" — незнакомая Morfologik'у фамилия/организация
    # с заглавной буквы не в начале предложения — не должна флажиться.
    match = {
        "message": "Возможно, опечатка",
        "rule": {"category": {"id": "TYPOS"}},
        "context": {"text": "ООО Монтажсвязьстрой предоставляет услуги", "offset": 4, "length": 16},
    }
    assert languagetool._is_proper_noun_false_positive(match) is True


def test_check_keeps_capitalized_typo_at_sentence_start() -> None:
    match = {
        "message": "Возможно, опечатка",
        "rule": {"category": {"id": "TYPOS"}},
        "context": {"text": "Неправльно написано слово.", "offset": 0, "length": 10},
    }
    assert languagetool._is_proper_noun_false_positive(match) is False


def test_check_keeps_lowercase_typo_mid_sentence() -> None:
    match = {
        "message": "Возможно, опечатка",
        "rule": {"category": {"id": "TYPOS"}},
        "context": {"text": "Эта неправльно написано.", "offset": 4, "length": 10},
    }
    assert languagetool._is_proper_noun_false_positive(match) is False


def test_check_does_not_filter_non_typos_category() -> None:
    # Фильтр должен применяться только к TYPOS — стилистическая находка на
    # заглавном слове в середине предложения не про орфографию.
    match = {
        "message": "тест",
        "rule": {"category": {"id": "STYLE"}},
        "context": {"text": "ООО Монтажсвязьстрой предоставляет услуги", "offset": 4, "length": 16},
    }
    assert languagetool._is_proper_noun_false_positive(match) is False
