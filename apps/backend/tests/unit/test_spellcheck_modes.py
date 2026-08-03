"""Быстрый и глубокий режимы проверки орфографии.

Замер на 29 намеренно заложенных ошибках в четырёх деловых письмах:
LanguageTool 14/29 за 1,7 с, модель 16/29 за 117 с, вместе 23/29. Ловят они
РАЗНОЕ, поэтому быстрый режим не заменяет глубокий, а даёт мгновенный ответ
там, где ждать две минуты на страницу незачем.
"""

from __future__ import annotations

import asyncio

import pytest
from fire_safety_backend import config
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


# --- Рычаги качества: порция, подсказки LanguageTool, отказ от переписывания ---
# Замер на 19 намеренно заложенных ошибках (одна модель, один промпт, менялся
# только размер куска): 300 слов → 5 из 19, ≈4 предложения → 16 из 19.


def test_model_is_not_asked_to_rewrite_the_text() -> None:
    """Модель тратила выдачу на переписывание фрагмента целиком вместо поиска
    ошибок. Текст теперь собирается применением правок."""
    from fire_safety_backend.pipelines._prompts import load_prompt

    assert "corrected_text" not in load_prompt("spellcheck")


def test_chunk_is_a_sentence_or_two_not_a_page() -> None:
    """Главный рычаг качества. На куске в 300 слов модель находит две-три
    ошибки и останавливается, пропуская даже «обьекте»."""
    assert config.SPELLCHECK_CHUNK_WORDS <= 40


def test_known_errors_are_shown_to_the_model() -> None:
    """LanguageTool отрабатывает до модели; раньше его находки использовались
    только для дедупликации после, и модель искала вслепую."""
    chunk = "Работы на обьекте будут проводится в срок."
    lt = [{"before": "обьекте", "after": "объекте"}]
    prompt = spellcheck._with_known_errors(chunk, lt)
    assert chunk in prompt
    assert "обьекте" in prompt.split(chunk, 1)[1], "находка LT должна быть показана"


def test_known_errors_hint_redirects_the_model_to_punctuation() -> None:
    """Мягкой формулировки не хватало: во фрагменте с опечаткой модель
    называла ровно эту опечатку и останавливалась, пропуская обращение и
    вводное слово в том же предложении (замерено на размеченном наборе,
    scripts/evaluate_spellcheck.py). Поэтому подсказка не просит «не
    повторять», а прямо переназначает задачу на пунктуацию."""
    chunk = "Уважаемый Иван Иванович работы на обьекте выполнены."
    lt = [{"before": "обьекте", "after": "объекте"}]
    hint = spellcheck._with_known_errors(chunk, lt).split(chunk, 1)[1]
    assert "ПУНКТУАЦИЯ" in hint
    assert "обращении" in hint


def test_errors_from_other_chunks_are_not_shown() -> None:
    """Подсказка про фрагмент, которого в этом куске нет, только сбивает."""
    chunk = "Работы завершены."
    lt = [{"before": "заблоговременно", "after": "заблаговременно"}]
    assert spellcheck._with_known_errors(chunk, lt) == chunk


def test_no_known_errors_leaves_the_chunk_untouched() -> None:
    chunk = "Текст без ошибок."
    assert spellcheck._with_known_errors(chunk, []) == chunk
