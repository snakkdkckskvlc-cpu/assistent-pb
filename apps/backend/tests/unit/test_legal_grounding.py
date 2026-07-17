"""Юнит-тесты grounded-цитирования и quote-anchoring в юр. анализе.

См. pipelines/legal.py::generate_short_id / _verify_quote и
docs/08-references.md (идеи из private-gpt и OpenContracts).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fire_safety_backend.pipelines import legal as legal_module
from fire_safety_backend.pipelines.legal import (
    _assign_chunk_ids,
    _resolve_chunk_id,
    _verify_quote,
    generate_short_id,
)

if TYPE_CHECKING:
    import pytest


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


def test_verify_quote_offset_is_in_original_text() -> None:
    # Регресс код-ревью (находка №10): offset раньше считался в схлопнутой
    # по пробелам копии текста — на реальном договоре с двойными пробелами/
    # переносами строк это давало offset, указывающий не туда в оригинале.
    source = "Преамбула.\n\nПункт   1.2: оплата производится в срок."
    found, offset = _verify_quote("Пункт 1.2: оплата производится", source)
    assert found is True
    assert offset is not None
    assert source[offset : offset + len("Пункт")] == "Пункт"


def test_verify_quote_handles_regex_metacharacters() -> None:
    # Цитаты из договоров часто содержат точки/скобки — не должны ломать
    # внутренний regex-поиск (re.escape по каждому слову).
    source = "См. п. 4.2 (в редакции доп. соглашения №1) договора."
    found, offset = _verify_quote("п. 4.2 (в редакции", source)
    assert found is True
    assert offset is not None


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


def test_assign_chunk_ids_no_collision_uses_first_id() -> None:
    chunks = [{"source": "a.txt", "text": "1"}, {"source": "b.txt", "text": "2"}]
    result = _assign_chunk_ids(chunks)
    assert len(result) == 2
    assert chunks[0] in result.values()
    assert chunks[1] in result.values()


def test_assign_chunk_ids_handles_collision(monkeypatch: pytest.MonkeyPatch) -> None:
    # Регресс код-ревью (находка №15): раньше построение через dict
    # comprehension молча теряло чанк при коллизии коротких ID.
    calls = {"n": 0}

    def colliding_short_id(seed: str, length: int = 4) -> str:
        calls["n"] += 1
        if calls["n"] <= 2:
            return "SAME"
        return f"UNIQ{calls['n']}"

    monkeypatch.setattr(legal_module, "generate_short_id", colliding_short_id)
    chunks = [{"source": "a.txt", "text": "1"}, {"source": "b.txt", "text": "2"}]
    result = _assign_chunk_ids(chunks)

    assert len(result) == 2, "оба чанка должны попасть в результат, ни один не потерян"
    assert chunks[0] in result.values()
    assert chunks[1] in result.values()
