"""Тесты гибридного поиска: слияние вектора и BM25, фильтры, нормализация.

Замер, ради которого гибрид вводился (живой индекс, 3334 чанка): на запросе
«неустойка явно несоразмерна последствиям нарушения» чистый вектор возвращал
СП 484.1311500.2020 «Сигнализация» (косинус 0.785), а BM25 — ГК РФ ст. 333
первой с отрывом вчетверо (43.13 против 11.35).
"""

from __future__ import annotations

import pytest
from fire_safety_rag.hybrid_retriever import (
    HybridRetriever,
    _matches_where,
    _normalize_bm25,
    _normalize_vector,
    _tokenize_query,
)

# --- Токенизация ------------------------------------------------------------


def test_query_tokens_are_deduplicated() -> None:
    """BM25Okapi суммирует вклад каждого вхождения термина в запросе. Без
    дедупликации строка из 24 повторов набирала 104.9 балла на одних предлогах
    — больше, чем реальный кусок договора на осмысленном поиске."""
    assert _tokenize_query("борщ борщ борщ сметана") == ["борщ", "сметана"]


def test_tokenizer_keeps_digits() -> None:
    """«333», «123», «5.13130» — основная различающая сила в нормативке."""
    tokens = _tokenize_query("статья 333 ГК РФ и СП 5.13130")
    assert "333" in tokens
    assert "5" in tokens and "13130" in tokens


def test_tokenizer_lowercases() -> None:
    assert _tokenize_query("НЕУСТОЙКА") == ["неустойка"]


# --- where-фильтр -----------------------------------------------------------


def test_where_none_matches_everything() -> None:
    assert _matches_where({"doc_type": "sp"}, None) is True
    assert _matches_where({}, {}) is True


def test_where_ne_passes_document_without_the_key() -> None:
    """Поведение ChromaDB 0.5.23, проверенное прямым запросом: документ БЕЗ
    поля проходит фильтр $ne. Лексическая половина обязана вести себя так же,
    иначе выдачи двух половин разойдутся."""
    assert _matches_where({"source": "a.txt"}, {"status": {"$ne": "superseded"}}) is True
    assert _matches_where({"status": "actual"}, {"status": {"$ne": "superseded"}}) is True
    assert _matches_where({"status": "superseded"}, {"status": {"$ne": "superseded"}}) is False


def test_where_in_requires_the_key() -> None:
    cond = {"doc_type": {"$in": ["code", "federal_law"]}}
    assert _matches_where({"doc_type": "code"}, cond) is True
    assert _matches_where({"doc_type": "sp"}, cond) is False
    assert _matches_where({"source": "a.txt"}, cond) is False


def test_where_and_combines_conditions() -> None:
    """Ровно тот фильтр, который строит _norm_filter_for в юр-анализе."""
    cond = {
        "$and": [
            {"status": {"$ne": "superseded"}},
            {"doc_type": {"$in": ["code", "federal_law", "government_decree"]}},
        ]
    }
    assert _matches_where({"status": "actual", "doc_type": "code"}, cond) is True
    assert _matches_where({"status": "superseded", "doc_type": "code"}, cond) is False
    assert _matches_where({"status": "actual", "doc_type": "sp"}, cond) is False


def test_where_or_and_bare_value() -> None:
    assert _matches_where({"doc_type": "sp"}, {"doc_type": "sp"}) is True
    assert _matches_where({"doc_type": "sp"}, {"doc_type": "code"}) is False
    cond = {"$or": [{"doc_type": "sp"}, {"doc_type": "gost"}]}
    assert _matches_where({"doc_type": "gost"}, cond) is True
    assert _matches_where({"doc_type": "code"}, cond) is False


def test_where_unknown_operator_does_not_drop_the_document() -> None:
    """Неизвестный оператор — это наша недоработка, а не свойство документа.
    Молча выкидывать из выдачи весь корпус нельзя."""
    assert _matches_where({"x": 1}, {"x": {"$regex": "nope"}}) is True


# --- Нормализация -----------------------------------------------------------


def test_vector_normalization_is_absolute_not_min_max() -> None:
    """Ключевая правка. Косинусы e5 лежат в узкой полосе, и min-max по выдаче
    растягивал разброс в девять тысячных на весь диапазон 0..1 — из-за чего
    ст. 333, найденная BM25 с отрывом вчетверо, не попадала в выдачу вовсе."""
    norm = _normalize_vector([0.780, 0.785, 0.789])
    # Все три — шум чуть выше пола 0.75, и все три обязаны остаться низкими.
    assert all(n < 0.25 for n in norm), norm
    # Min-max дал бы ровно [0.0, 0.5, 1.0] — проверяем, что это НЕ так.
    assert norm[-1] < 0.9


def test_vector_normalization_clamps_to_unit_range() -> None:
    assert _normalize_vector([0.5])[0] == 0.0
    assert _normalize_vector([0.99])[0] == 1.0


def test_weak_lexical_field_is_damped() -> None:
    """Запрос «рецепт борща» давал максимум BM25 = 8.4 — ничего общего с
    корпусом. Без приглушения он получал бы ровно 1.0, как точное попадание
    в ст. 333 с баллом 43."""
    weak = _normalize_bm25([8.4, 8.3, 8.2])
    strong = _normalize_bm25([43.1, 11.3, 10.5])
    assert weak[0] < 0.4
    assert strong[0] == pytest.approx(1.0)


def test_bm25_zero_scores_stay_zero() -> None:
    assert _normalize_bm25([0.0, 0.0]) == [0.0, 0.0]
    assert _normalize_bm25([]) == []


# --- Слияние выдач ----------------------------------------------------------


def _hit(source: str, text: str, score: float, article: str = "") -> dict:
    return {
        "text": text,
        "source": source,
        "score": score,
        "act_number": "",
        "article": article,
        "chapter": "",
        "doc_type": "code",
        "effective_date": "",
        "status": "actual",
    }


def _bm25_hit(source: str, text: str, raw: float, article: str = "") -> dict:
    hit = _hit(source, text, 0.0, article)
    hit.update({"vector_score": 0.0, "bm25_score": raw})
    return hit


@pytest.fixture
def retriever() -> HybridRetriever:
    r = HybridRetriever.__new__(HybridRetriever)  # без обращения к ChromaDB
    r._docs = []
    r._metas = []
    r._key_index = {}
    return r


def test_lexical_only_hit_beats_vector_noise(retriever: HybridRetriever) -> None:
    """Регрессия на измеренный провал: ст. 333 найдена только лексически, а
    векторные кандидаты — шум в полосе 0.78. Статья обязана быть первой."""
    vector = [
        _hit("SP5.txt", "про сигнализацию", 0.789),
        _hit("SP4.txt", "про огнестойкость", 0.783),
    ]
    lexical = [_bm25_hit("GK.txt", "Статья 333. Уменьшение неустойки", 43.1, "Статья 333")]
    fused = retriever._fuse(vector, lexical, k=8, scores=None)
    assert fused[0]["source"] == "GK.txt"
    assert fused[0]["article"] == "Статья 333"


def test_strong_vector_hit_beats_weak_lexical(retriever: HybridRetriever) -> None:
    """Обратная сторона: перефразированный пункт без общих слов находит только
    вектор, и слабая лексика не должна его вытеснять."""
    vector = [_hit("GK.txt", "ответственность подрядчика", 0.907, "Статья 754")]
    lexical = [_bm25_hit("SP1.txt", "случайное совпадение слов", 6.0)]
    fused = retriever._fuse(vector, lexical, k=8, scores=None)
    assert fused[0]["source"] == "GK.txt"


def test_hit_found_by_both_halves_is_merged_once(retriever: HybridRetriever) -> None:
    vector = [_hit("GK.txt", "Статья 333. Уменьшение неустойки", 0.88, "Статья 333")]
    lexical = [_bm25_hit("GK.txt", "Статья 333. Уменьшение неустойки", 43.1, "Статья 333")]
    fused = retriever._fuse(vector, lexical, k=8, scores=None)
    assert len(fused) == 1
    # Оба сигнала сохранены у одной записи, а не разложены по двум.
    assert fused[0]["vector_score"] == pytest.approx(0.88)
    assert fused[0]["bm25_score"] == pytest.approx(43.1)


def test_fuse_respects_top_k(retriever: HybridRetriever) -> None:
    vector = [_hit(f"f{i}.txt", f"текст {i}", 0.80 + i / 1000) for i in range(20)]
    assert len(retriever._fuse(vector, [], k=8, scores=None)) == 8


def test_fuse_on_empty_input(retriever: HybridRetriever) -> None:
    assert retriever._fuse([], [], k=8, scores=None) == []


def test_raw_scores_are_exposed_for_confidence_check(retriever: HybridRetriever) -> None:
    """Итоговый score нормализован ВНУТРИ выдачи и между запросами несравним;
    судить об уверенности можно только по сырым баллам, поэтому они обязаны
    доезжать до потребителя."""
    fused = retriever._fuse([_hit("a.txt", "текст", 0.85)], [], k=8, scores=None)
    assert fused[0]["vector_score"] == pytest.approx(0.85)
    assert "bm25_score" in fused[0]
