"""Быстрый и глубокий режимы проверки орфографии.

Замер на 29 намеренно заложенных ошибках в четырёх деловых письмах:
LanguageTool 14/29 за 1,7 с, модель 16/29 за 117 с, вместе 23/29. Ловят они
РАЗНОЕ, поэтому быстрый режим не заменяет глубокий, а даёт мгновенный ответ
там, где ждать две минуты на страницу незачем.
"""

from __future__ import annotations

import asyncio

import pytest
from fire_safety_backend.infrastructure import languagetool, llm
from fire_safety_backend.pipelines import spellcheck


@pytest.fixture
def lt_finds_two(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_check(text: str, language: str = "ru-RU") -> list[dict]:
        return [
            {
                "type": "орфография",
                "before": "обьекте",
                "after": "объекте",
                "source": "languagetool",
            },
            {
                "type": "пунктуация",
                "before": "работам но",
                "after": "работам, но",
                "source": "languagetool",
            },
        ]

    monkeypatch.setattr(languagetool, "check", fake_check)


def test_fast_mode_does_not_call_the_model(lt_finds_two, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        calls.append(user)
        return {"errors": [], "corrected_text": user}

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    result = asyncio.run(
        spellcheck.run_spellcheck("Работы на обьекте, приступаем к работам но позже.", deep=False)
    )
    assert calls == [], "быстрый режим не должен обращаться к модели"
    assert len(result["errors"]) == 2
    assert result["stats"]["chunks_processed"] == 0


def test_fast_mode_still_returns_corrected_text(lt_finds_two) -> None:
    """Модель не вызывается, но исправленный текст показать надо — он
    собирается применением найденных правок."""
    result = asyncio.run(
        spellcheck.run_spellcheck("Работы на обьекте, приступаем к работам но позже.", deep=False)
    )
    assert "объекте" in result["corrected_text"]
    assert "работам, но" in result["corrected_text"]
    assert "обьекте" not in result["corrected_text"]


def test_deep_mode_calls_the_model(lt_finds_two, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        calls.append(user)
        return {
            "errors": [{"type": "пунктуация", "before": "о том что", "after": "о том, что"}],
            "corrected_text": user,
        }

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    result = asyncio.run(spellcheck.run_spellcheck("Уведомляем о том что работы идут.", deep=True))
    assert calls, "глубокий режим обязан звать модель"
    # Находки обоих источников попадают в общий список.
    assert len(result["errors"]) == 3


def test_longest_fragment_is_replaced_first() -> None:
    """Короткий фрагмент может быть частью длинного: заменив его первым, мы
    разрушили бы длинный и потеряли правку."""
    errors = [
        {"before": "течении", "after": "течение"},
        {"before": "в течении месяца", "after": "в течение месяца"},
    ]
    assert spellcheck._apply_to_text("Срок в течении месяца.", errors) == "Срок в течение месяца."


def test_self_containing_replacement_is_skipped_in_text() -> None:
    assert spellcheck._apply_to_text("месяца", [{"before": "месяца", "after": "месяца работ"}]) == (
        "месяца"
    )
