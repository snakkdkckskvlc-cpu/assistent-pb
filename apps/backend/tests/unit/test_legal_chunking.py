"""Тесты нарезки договора под окно модели.

Регрессия на реальный баг: юр. анализ отправлял договор целиком, Ollama при
превышении num_ctx молча отбрасывала начало запроса ВМЕСТЕ С СИСТЕМНЫМ
ПРОМПТОМ, и модель возвращала не анализ рисков, а пересказ хвоста документа.
Замер на живом прогоне: из ~14 000 отправленных токенов обрабатывалось 4 098.
"""

from __future__ import annotations

import pytest
from fire_safety_backend import config
from fire_safety_backend.pipelines import legal


def test_part_budget_fits_into_window() -> None:
    """Бюджет на текст + ответ + промпт + нормы обязан помещаться в окно."""
    prompt = "П" * 3000
    budget_tokens = legal._input_budget_tokens(prompt)
    reserved = (
        config.LLM_NUM_PREDICT_LEGAL_PART
        + legal._SAFETY_TOKENS
        + legal._estimate_tokens(prompt)
        + legal._RAG_CHUNKS_PER_PART * int(legal._RAG_CHUNK_MAX_CHARS / legal._CHARS_PER_TOKEN)
    )
    assert budget_tokens + reserved <= config.LLM_NUM_CTX_LEGAL


def test_estimate_is_conservative_versus_measured_density() -> None:
    """Оценка должна быть ПЕССИМИСТИЧНЕЕ замеренной плотности.

    Замерено на договорах НЛМК: 2.57 символа на токен. Если константа станет
    больше замеренного, оценка начнёт занижать реальный расход и часть снова
    сможет не влезть в окно.
    """
    assert legal._CHARS_PER_TOKEN < 2.57
    # Замерено 3.78 токена на слово, но на таблицах реквизитов доходит до 4.67.
    assert legal._TOKENS_PER_WORD >= 4.6


def test_oversized_part_is_split_further() -> None:
    """Страховка поверх расчёта по словам: кусок, не влезающий по символам,
    обязан быть раздроблен, а не отправлен как есть."""
    prompt = "П" * 1000
    budget = legal._input_budget_tokens(prompt)
    huge = "слово " * (budget * 3)  # заведомо больше бюджета

    parts = legal._split_oversized_parts([huge], prompt)

    assert len(parts) > 1
    for p in parts:
        assert legal._estimate_tokens(p) <= budget, "часть всё ещё не влезает в окно"


def test_small_parts_are_not_split() -> None:
    prompt = "П" * 1000
    small = "короткий пункт договора"
    assert legal._split_oversized_parts([small], prompt) == [small]


def test_splitting_stops_at_minimum_size() -> None:
    """Дробление не должно уходить в бесконечность на патологическом входе:
    ниже минимального размера кусок оставляем как есть."""
    prompt = "П" * 1000
    # Слово длиной с целый бюджет — сколько ни дроби, по символам не влезет.
    monstrous = "ы" * (legal._input_budget_tokens(prompt) * 10)
    parts = legal._split_oversized_parts([monstrous], prompt)
    assert len(parts) == 1


def test_merge_findings_drops_duplicates_across_parts() -> None:
    """Один и тот же пункт может всплыть в двух соседних частях — показывать
    его дважды не нужно."""
    a = [{"цитата_из_договора": "неустойка  2%  за каждый день", "в_чём_риск": "много"}]
    b = [
        {"цитата_из_договора": "Неустойка 2% за КАЖДЫЙ день", "в_чём_риск": "дубль"},
        {"цитата_из_договора": "оплата 60 дней", "в_чём_риск": "кассовый разрыв"},
    ]
    merged = legal._merge_findings([a, b])
    assert len(merged) == 2
    assert merged[0]["в_чём_риск"] == "много"


def test_merge_findings_keeps_distinct_without_quotes() -> None:
    a = [{"в_чём_риск": "риск один"}]
    b = [{"в_чём_риск": "риск два"}]
    assert len(legal._merge_findings([a, b])) == 2


def test_merge_summaries_dedups_and_joins() -> None:
    s1 = {
        "плюсы_для_компании": ["гарантия 12 мес"],
        "минусы_для_компании": ["штрафы"],
        "общий_вывод": "Требует правок.",
    }
    s2 = {
        "плюсы_для_компании": ["Гарантия 12 мес"],
        "минусы_для_компании": ["нет лимита"],
        "общий_вывод": "Требует правок.",
    }
    merged = legal._merge_summaries([s1, s2])
    assert merged["плюсы_для_компании"] == ["гарантия 12 мес"]
    assert set(merged["минусы_для_компании"]) == {"штрафы", "нет лимита"}
    assert merged["общий_вывод"] == "Требует правок."


def test_merge_summaries_survives_garbage() -> None:
    assert legal._merge_summaries([None, "строка", {}])["плюсы_для_компании"] == []


async def test_run_legal_analysis_splits_and_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сквозной тест: длинный договор уходит НЕСКОЛЬКИМИ запросами, находки
    из всех частей попадают в общий результат."""
    calls: list[str] = []

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        calls.append(user)
        idx = len(calls)
        return {
            "находки": [
                {
                    "критичность": "красный",
                    "цитата_из_договора": f"пункт {idx}",
                    "в_чём_риск": f"риск {idx}",
                }
            ],
            "сводка": {"плюсы_для_компании": [], "минусы_для_компании": [], "общий_вывод": "ok"},
        }

    monkeypatch.setattr(legal.llm, "chat_json", fake_chat_json)
    monkeypatch.setattr(legal, "retrieve_many", lambda queries, top_k=None: [[] for _ in queries])

    # Договор заведомо больше одного окна.
    text = "Пункт договора об ответственности сторон. " * 3000
    result = await legal.run_legal_analysis(text)

    assert len(calls) > 1, "длинный договор обязан уйти несколькими запросами"
    assert result["_частей"] == len(calls)
    assert len(result["находки"]) == len(calls)
    # Каждый запрос обязан нести системный промпт и не превышать окно.
    for user_msg in calls:
        assert legal._estimate_tokens(user_msg) <= config.LLM_NUM_CTX_LEGAL
