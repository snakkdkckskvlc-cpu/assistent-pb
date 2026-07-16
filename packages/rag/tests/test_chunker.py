"""Юнит-тесты sentence-aware чанкера RAG (без ChromaDB)."""

from __future__ import annotations

from fire_safety_rag.chunking import chunk_sentences


def test_chunk_short_text() -> None:
    text = "Первое предложение. Второе предложение. Третье предложение."
    chunks = chunk_sentences(text, max_words=100)
    assert len(chunks) == 1


def test_chunk_empty() -> None:
    assert chunk_sentences("", max_words=10) == []


def test_chunk_splits_on_sentence_boundaries() -> None:
    # 5 предложений по 4 слова — при лимите в 10 слов должно уложиться
    # по 2 предложения на чанк, без разрезания предложения пополам.
    sentences = [f"Это предложение номер {i}." for i in range(5)]
    text = " ".join(sentences)
    chunks = chunk_sentences(text, max_words=10, overlap_words=0)
    assert len(chunks) > 1
    for chunk in chunks:
        # Каждый чанк должен состоять из целых предложений исходного текста.
        for sentence in sentences:
            assert sentence not in chunk or chunk.count(sentence) <= 1
        assert chunk.strip().endswith(".")


def test_chunk_with_overlap_carries_trailing_sentence() -> None:
    sentences = [f"Предложение номер {i} тут." for i in range(6)]
    text = " ".join(sentences)
    no_overlap = chunk_sentences(text, max_words=10, overlap_words=0)
    with_overlap = chunk_sentences(text, max_words=10, overlap_words=5)
    assert len(with_overlap) >= len(no_overlap)
    # Последнее предложение первого чанка должно повториться в начале второго.
    first_chunk_sentences = [s for s in sentences if s in with_overlap[0]]
    assert first_chunk_sentences
    assert first_chunk_sentences[-1] in with_overlap[1]


def test_chunk_force_splits_oversized_single_sentence() -> None:
    # Одно "предложение" без точек длиннее лимита — нет границ предложений,
    # чанкер обязан всё равно порезать по словам, а не вернуть один гигантский чанк.
    text = " ".join(str(i) for i in range(30))
    chunks = chunk_sentences(text, max_words=10)
    assert len(chunks) == 3
    assert chunks[0].split()[:3] == ["0", "1", "2"]
    assert chunks[1].split()[0] == "10"
