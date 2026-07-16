"""Юнит-тесты grounded-цитирования и quote-anchoring в юр. анализе.

См. pipelines/legal.py::generate_short_id / _verify_quote и
docs/08-references.md (идеи из private-gpt и OpenContracts).
"""

from __future__ import annotations

from fire_safety_backend.pipelines.legal import (
    _resolve_chunk_id,
    _verify_quote,
    generate_short_id,
)


def test_generate_short_id_deterministic() -> None:
    assert generate_short_id("123-ФЗ.txt|0") == generate_short_id("123-ФЗ.txt|0")


def test_generate_short_id_differs_for_different_seeds() -> None:
    assert generate_short_id("a") != generate_short_id("b")


def test_generate_short_id_has_requested_length() -> None:
    assert len(generate_short_id("seed", length=4)) == 4
    assert len(generate_short_id("seed", length=6)) == 6


def test_verify_quote_found() -> None:
    source = "Между сторонами заключен договор subподряда №17 от 12.01.2026г."
    found, offset = _verify_quote("договор subподряда №17", source)
    assert found is True
    assert offset is not None


def test_verify_quote_not_found() -> None:
    found, offset = _verify_quote("этого текста тут нет вообще", "Совсем другой текст.")
    assert found is False
    assert offset is None


def test_verify_quote_tolerates_whitespace_differences() -> None:
    # Модель иногда схлопывает/добавляет пробелы и переносы строк при цитировании.
    source = "Пункт   1.2:\nОплата производится в течение 10 дней."
    found, _ = _verify_quote("Пункт 1.2: Оплата производится", source)
    assert found is True


def test_verify_quote_empty_is_not_found() -> None:
    found, offset = _verify_quote("", "любой текст")
    assert found is False
    assert offset is None


def test_resolve_chunk_id_exact_match() -> None:
    chunk = {"source": "123-ФЗ.txt", "text": "..."}
    chunk_ids = {"GGVR": chunk}
    assert _resolve_chunk_id("GGVR", chunk_ids) is chunk


def test_resolve_chunk_id_tolerates_bracket_and_filename() -> None:
    # Живой ответ qwen2.5:7b-instruct вернул именно такую строку вместо
    # голого ID, несмотря на инструкцию в промпте — валидатор должен всё
    # равно опознать реально существующий ID внутри неё.
    chunk = {"source": "GK_RF_part1_dogovor.txt", "text": "..."}
    chunk_ids = {"GGVR": chunk}
    assert _resolve_chunk_id("[GGVR] GK_RF_part1_dogovor.txt", chunk_ids) is chunk


def test_resolve_chunk_id_no_match_returns_none() -> None:
    chunk_ids = {"GGVR": {"source": "x", "text": "..."}}
    assert _resolve_chunk_id("ZZZZ", chunk_ids) is None


def test_resolve_chunk_id_empty_string_returns_none() -> None:
    chunk_ids = {"GGVR": {"source": "x", "text": "..."}}
    assert _resolve_chunk_id("", chunk_ids) is None
