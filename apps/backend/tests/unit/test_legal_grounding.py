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


# --- Верификация номеров статей (регрессия: ст. 395 вместо ст. 333) ---


def test_norm_ref_confirmed_when_article_in_context() -> None:
    ctx = [
        {
            "article": "Статья 333",
            "text": "Статья 333. Уменьшение неустойки. Если явно несоразмерна…",
        }
    ]
    assert legal_module._verify_article_reference("ст. 333 ГК РФ", ctx) == "подтверждена"


def test_norm_ref_flagged_when_article_absent_from_context() -> None:
    """Замеренный случай: модель сослалась на ст. 395, которой в переданном
    контексте не было — там была только ст. 333."""
    ctx = [{"article": "Статья 333", "text": "Статья 333. Уменьшение неустойки."}]
    assert legal_module._verify_article_reference("статья 395 ГК РФ", ctx) == "не_в_контексте"


def test_norm_ref_with_point_prefix_is_confirmed() -> None:
    ctx = [{"article": "Статья 401", "text": "Статья 401. Основания ответственности."}]
    assert legal_module._verify_article_reference("п. 2 ст. 401 ГК РФ", ctx) == "подтверждена"


def test_norm_ref_not_checked_when_no_number() -> None:
    ctx = [{"article": "Статья 333", "text": "Статья 333. Уменьшение неустойки."}]
    assert (
        legal_module._verify_article_reference("требует проверки юристом", ctx) == "не_проверялась"
    )
    assert legal_module._verify_article_reference("", ctx) == "не_проверялась"


def test_norm_ref_matches_sp_numbering() -> None:
    ctx = [{"article": "5.1.2", "text": "5.1.2. Извещатели пожарные следует размещать."}]
    assert legal_module._verify_article_reference("п. 5.1.2 СП 5.13130.2009", ctx) == "подтверждена"


def test_norm_ref_empty_context_is_not_confirmed() -> None:
    assert legal_module._verify_article_reference("ст. 333 ГК РФ", []) == "не_в_контексте"


# --- Обрезка норм по границе статьи (регрессия: 39% ст. 333) ---


def test_trim_norm_keeps_whole_articles() -> None:
    """Влезшие статьи отдаются целиком, не влезшая отбрасывается целиком.

    Раньше обрезка шла по счётчику символов и рубила статью на полуслове: из
    ст. 333 ГК РФ до модели доезжало 39%, обрыв приходился ровно на условие о
    снижении неустойки для предпринимателей.
    """
    # Маркеры латиницей — в русском тексте заголовков они не встречаются,
    # иначе счёт сбивает буква «а» из самого слова «Статья».
    text = (
        "Статья 330. Понятие неустойки\n" + "X" * 400 + "\n"
        "Статья 333. Уменьшение неустойки\n" + "Y" * 400 + "\n"
        "Статья 395. Проценты\n" + "Z" * 400
    )
    trimmed = legal_module._trim_norm_text(text, 1000)

    assert len(trimmed) <= 1000
    # Две первые статьи влезли — обе присутствуют полностью.
    assert trimmed.count("X") == 400
    assert trimmed.count("Y") == 400
    # Третья не влезала — отброшена целиком, а не обрезана на полуслове.
    assert "Статья 395" not in trimmed
    assert "Z" not in trimmed


def test_trim_norm_short_text_unchanged() -> None:
    text = "Статья 1. Короткая статья"
    assert legal_module._trim_norm_text(text, 1800) == text


def test_trim_norm_without_markup_cuts_on_sentence() -> None:
    text = "Первое предложение. " * 200
    trimmed = legal_module._trim_norm_text(text, 500)
    assert len(trimmed) <= 500
    assert trimmed.rstrip().endswith(".")


# --- Автоматическое повышение критичности -----------------------------------
# Регрессия на живой прогон договора ДГ-1010-1548-05-26: модель поставила
# «жёлтый» 18 находкам из 19, включая неустойку 2 % за день просрочки и штраф
# 20 % от стоимости. Красных не было ни одной, хотя в ручном разборе они есть.


def _finding(quote: str, crit: str = "жёлтый") -> dict:
    return {"критичность": crit, "цитата_из_договора": quote}


def test_daily_penalty_above_threshold_becomes_red() -> None:
    f = _finding(
        "За несвоевременное выполнение работ Подрядчик уплачивает Заказчику "
        "неустойку в размере 2% за каждый день просрочки от суммы договора."
    )
    legal_module._escalate_severity(f)
    assert f["критичность"] == "красный"
    assert "730" in f["_критичность_повышена"]  # 2 % в день = 730 % годовых


def test_daily_penalty_with_comma_decimal_and_calendar_day() -> None:
    f = _finding("пеня в размере 0,5 % за каждый календарный день просрочки")
    legal_module._escalate_severity(f)
    assert f["критичность"] == "красный"


def test_ordinary_daily_penalty_stays_with_model() -> None:
    """0,1 % в день — обычная договорная ставка, повышать её нельзя."""
    f = _finding("неустойку в размере 0,1 % за каждый день просрочки")
    legal_module._escalate_severity(f)
    assert f["критичность"] == "жёлтый"
    assert "_критичность_повышена" not in f


def test_fixed_fine_above_threshold_becomes_red() -> None:
    f = _finding(
        "Заказчик вправе требовать уплаты штрафа в размере 20 % от стоимости уступленных прав"
    )
    legal_module._escalate_severity(f)
    assert f["критичность"] == "красный"
    assert "20" in f["_критичность_повышена"]


def test_small_fine_is_not_escalated() -> None:
    f = _finding("Подрядчик уплачивает штраф в размере 5 % от стоимости работ")
    legal_module._escalate_severity(f)
    assert f["критичность"] == "жёлтый"


def test_escalation_never_downgrades() -> None:
    """Правило работает только вверх: «красный» от модели остаётся красным
    даже там, где цифр в цитате нет вовсе."""
    f = _finding("Ответственность Подрядчика не ограничена", crit="красный")
    legal_module._escalate_severity(f)
    assert f["критичность"] == "красный"
    assert "_критичность_повышена" not in f


def test_percent_without_day_marker_is_not_a_daily_rate() -> None:
    """«Аванс 30 % от цены договора» — это не ставка неустойки."""
    f = _finding("Заказчик выплачивает аванс в размере 30 % от цены договора.")
    legal_module._escalate_severity(f)
    assert f["критичность"] == "жёлтый"


def test_percent_in_another_sentence_does_not_leak_into_daily_rate() -> None:
    """Точка обрывает поиск: процент из одного предложения не должен
    склеиваться со словами «за каждый день» из следующего."""
    f = _finding("Аванс составляет 30 %. Работы сдаются за каждый день по акту.")
    legal_module._escalate_severity(f)
    assert f["критичность"] == "жёлтый"


def test_finding_without_quote_survives() -> None:
    f = {"критичность": "жёлтый"}
    legal_module._escalate_severity(f)
    assert f["критичность"] == "жёлтый"


# --- Флаг «релевантных норм не найдено» -------------------------------------
# Пороги взяты из замера на живом индексе (3334 чанка): реальные куски договора
# дают косинус 0.814–0.865 и BM25 69–110, заведомая чушь — 0.000–0.807 и 0–13.


def _chunk(vector: float, bm25: float) -> dict:
    return {"text": "норма", "source": "a.txt", "vector_score": vector, "bm25_score": bm25}


def test_low_confidence_when_both_signals_are_weak() -> None:
    assert legal_module._is_low_confidence([_chunk(0.767, 8.4)]) is True


def test_not_low_confidence_when_only_lexical_matches() -> None:
    """Ровно случай ст. 333: косинус в полосе шума, зато точное совпадение по
    словам. Выбросить такую норму значило бы обнулить смысл гибридного поиска."""
    assert legal_module._is_low_confidence([_chunk(0.789, 43.1)]) is False


def test_not_low_confidence_when_only_vector_matches() -> None:
    """Обратный случай: пункт перефразирован, общих слов с нормой нет."""
    assert legal_module._is_low_confidence([_chunk(0.865, 0.0)]) is False


def test_low_confidence_on_empty_result() -> None:
    assert legal_module._is_low_confidence([]) is True


def test_old_retriever_output_is_not_flagged() -> None:
    """У чанков без сырых баллов (векторный ретривер, fallback) судить не по
    чему — объявлять низкую уверенность на пустом месте нельзя."""
    assert legal_module._is_low_confidence([{"text": "норма", "source": "a.txt"}]) is False


def test_best_chunk_decides_not_the_first() -> None:
    weak_then_strong = [_chunk(0.77, 1.0), _chunk(0.90, 0.0)]
    assert legal_module._is_low_confidence(weak_then_strong) is False
