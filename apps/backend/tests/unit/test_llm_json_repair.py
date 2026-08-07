"""Ответ модели не по схеме: нормализация ключей и починка JSON.

Все случаи ниже — НАСТОЯЩИЕ, снятые с GigaChat3.1-10B на датасете договоров
(docs/05-quality/improvements-backlog.md, пункт 2.2). Модель давала заметно
лучший разбор, чем qwen2.5, но два договора из пяти терялись целиком, и
по-разному:

* договор 01 — валидный JSON, но ключи английские (`findings`,
  `quote_from_contract`), причём сводка в том же ответе с русскими. Пайплайн
  искал «находки», не находил и отдавал ПУСТОЙ отчёт. Для пользователя это
  «в договоре рисков нет» — тихий и правдоподобный отказ, худший из всех;
* договор 05 — лишняя кавычка перед ключом плюс рано закрытый корень. Задача
  падала с ошибкой после шести минут ожидания.

Ни то ни другое не лечится повторным запросом: в первом случае JSON валиден и
повторять нечего, во втором температура юр. анализа равна нулю, и повтор вернул
бы тот же самый текст. Поэтому чинится разбором ответа, а не перегенерацией.
"""

from __future__ import annotations

import json

import pytest
from fire_safety_backend.infrastructure.llm import LLMError, _parse_json_loose


def test_english_keys_are_renamed_to_the_schema() -> None:
    """Случай договора 01: разбор есть, но под чужими именами полей."""
    raw = json.dumps(
        {
            "findings": [
                {
                    "criticality": "красный",
                    "quote_from_contract": "Оплата в течение 90 дней",
                    "risk": "Слишком долгий срок",
                    "link_to_norm": "ст. 314 ГК РФ",
                    "source_fragment": "A1B2",
                    "edit_suggestion": "Сократить до 30 дней",
                }
            ],
            "summary": {"pros": [], "cons": ["Долгая оплата"], "conclusion": "С правками"},
        },
        ensure_ascii=False,
    )
    parsed = _parse_json_loose(raw)

    assert len(parsed["находки"]) == 1
    finding = parsed["находки"][0]
    assert finding["критичность"] == "красный"
    assert finding["цитата_из_договора"] == "Оплата в течение 90 дней"
    assert finding["в_чём_риск"] == "Слишком долгий срок"
    assert finding["ссылка_на_норму"] == "ст. 314 ГК РФ"
    assert finding["источник_фрагмента"] == "A1B2"
    assert finding["предложение_правки"] == "Сократить до 30 дней"
    assert parsed["сводка"]["минусы_для_компании"] == ["Долгая оплата"]


def test_russian_key_wins_when_model_sent_both() -> None:
    """Переименование не должно затирать то, что названо по схеме."""
    raw = '{"находки": [1], "findings": [2, 3]}'
    assert _parse_json_loose(raw)["находки"] == [1]


def test_mixed_language_answer_survives() -> None:
    """Ровно как на договоре 01: находки по-английски, сводка по-русски."""
    raw = (
        '{"findings": [{"criticality": "жёлтый"}], '
        '"сводка": {"общий_вывод": "Подписывать с правками"}}'
    )
    parsed = _parse_json_loose(raw)
    assert parsed["находки"][0]["критичность"] == "жёлтый"
    assert parsed["сводка"]["общий_вывод"] == "Подписывать с правками"


def test_duplicated_quote_before_key_is_repaired() -> None:
    """Случай договора 05, первая половина: `,""сводка":` вместо `,"сводка":`."""
    raw = '{"находки": [],""сводка": {"общий_вывод": "Подписывать"}}'
    assert _parse_json_loose(raw)["сводка"]["общий_вывод"] == "Подписывать"


def test_early_root_close_is_repaired() -> None:
    """Случай договора 05, вторая половина: корень закрыт на скобку раньше."""
    raw = '{"находки": [], "сводка": {"общий_вывод": "ок"}},"предложения_правки": [1]}'
    parsed = _parse_json_loose(raw)
    assert parsed["сводка"]["общий_вывод"] == "ок"
    assert parsed["предложения_правки"] == [1]


def test_both_defects_at_once() -> None:
    """На договоре 05 они были ВМЕСТЕ, и по одной ни та ни другая не спасала."""
    raw = '{"находки": [],""сводка": {"общий_вывод": "ок"}},"предложения_правки": [1]}'
    parsed = _parse_json_loose(raw)
    assert parsed["сводка"]["общий_вывод"] == "ок"
    assert parsed["предложения_правки"] == [1]


def test_trailing_comma_is_repaired() -> None:
    """Привычка из JavaScript, которую JSON не допускает."""
    assert _parse_json_loose('{"находки": [1, 2,],}')["находки"] == [1, 2]


def test_legitimate_double_brace_is_not_touched() -> None:
    """`}},"` — законная запись, и «ремонт» не должен её ломать.

    Именно поэтому рано закрытый корень чинится по позиции, которую сообщил
    парсер, а не заменой подстроки: слепая замена испортила бы верный ответ.
    """
    raw = '{"a": {"b": {"c": 1}}, "d": 2}'
    assert _parse_json_loose(raw) == {"a": {"b": {"c": 1}}, "d": 2}


def test_empty_string_in_array_is_not_treated_as_broken_key() -> None:
    """`,""` внутри массива строк — законно, чинить там нечего."""
    raw = '{"минусы_для_компании": ["есть", ""]}'
    assert _parse_json_loose(raw)["минусы_для_компании"] == ["есть", ""]


def test_markdown_fence_still_stripped() -> None:
    assert _parse_json_loose('```json\n{"находки": []}\n```')["находки"] == []


def test_hopeless_answer_still_raises() -> None:
    """Починка узкая по замыслу: то, что не чинится, обязано падать громко.

    Молча вернуть пустой словарь здесь нельзя — это и есть тот самый тихий
    отказ, из-за которого правка затевалась.
    """
    with pytest.raises(LLMError, match="невалидный JSON"):
        _parse_json_loose("это не JSON, а извинения модели")


def test_non_dict_json_passes_through_untouched() -> None:
    """Нормализация ключей применяется к словарю, список остаётся списком."""
    assert _parse_json_loose("[1, 2, 3]") == [1, 2, 3]


# --- Вторая ступень: повтор запроса ------------------------------------------


async def test_unrepairable_answer_triggers_one_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Поломки не сводятся к списку известных — нужен и повтор.

    Замерено: договор 01 в одном прогоне дал английские ключи, в другом —
    синтаксическую ошибку в другом месте. Повтор работает даже при нулевой
    температуре, потому что ответы модели между прогонами различаются.
    """
    from fire_safety_backend.infrastructure import llm

    answers = iter(["не JSON вовсе", '{"находки": [1]}'])
    calls = 0

    async def fake_chat(system: str, user: str, **kwargs) -> str:
        nonlocal calls
        calls += 1
        return next(answers)

    monkeypatch.setattr(llm, "chat", fake_chat)
    assert (await llm.chat_json("s", "u"))["находки"] == [1]
    assert calls == 2


async def test_retry_is_not_wasted_when_first_answer_is_fine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повтор удваивает время вызова — он не должен случаться просто так."""
    from fire_safety_backend.infrastructure import llm

    calls = 0

    async def fake_chat(system: str, user: str, **kwargs) -> str:
        nonlocal calls
        calls += 1
        return '{"находки": []}'

    monkeypatch.setattr(llm, "chat", fake_chat)
    await llm.chat_json("s", "u")
    assert calls == 1


async def test_two_broken_answers_still_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Молчать нельзя: если и повтор не помог, задача обязана упасть громко."""
    from fire_safety_backend.infrastructure import llm

    async def fake_chat(system: str, user: str, **kwargs) -> str:
        return "извинения модели вместо JSON"

    monkeypatch.setattr(llm, "chat", fake_chat)
    with pytest.raises(LLMError, match="невалидный JSON"):
        await llm.chat_json("s", "u")
