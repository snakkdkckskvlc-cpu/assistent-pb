"""Юнит-тесты чанкера RAG (без ChromaDB)."""
from __future__ import annotations

from fire_safety_rag.indexer import _chunk_text


def test_chunk_short_text() -> None:
    text = "слово " * 10
    chunks = _chunk_text(text, chunk_words=100, overlap=10)
    assert len(chunks) == 1


def test_chunk_with_overlap() -> None:
    text = " ".join(str(i) for i in range(30))
    chunks = _chunk_text(text, chunk_words=10, overlap=2)
    assert len(chunks) > 1
    # Первый чанк — числа 0..9
    assert chunks[0].split()[:3] == ["0", "1", "2"]
    # Второй чанк начинается с шага 10-2=8
    assert chunks[1].split()[0] == "8"


def test_chunk_empty() -> None:
    assert _chunk_text("", chunk_words=10, overlap=1) == []
