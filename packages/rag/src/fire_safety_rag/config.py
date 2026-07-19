"""Автономная конфигурация пакета fire_safety_rag.

Не зависит от других пакетов. Все настройки читаются из переменных окружения
с разумными значениями по умолчанию.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ROOT = Path.cwd() / "data"

CHROMA_DIR = Path(os.environ.get("RAG_CHROMA_DIR", _DEFAULT_ROOT / "chroma"))
CORPUS_DIR = Path(
    os.environ.get(
        "RAG_CORPUS_DIR",
        Path(__file__).resolve().parent.parent.parent / "corpus",
    )
)
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "intfloat/multilingual-e5-large")
COLLECTION_NAME = os.environ.get("RAG_COLLECTION", "legal_corpus")
LETTERS_COLLECTION_NAME = os.environ.get("RAG_LETTERS_COLLECTION", "letters_history")
TOP_K = int(os.environ.get("RAG_TOP_K", "5"))
CHUNK_TOKENS = int(os.environ.get("RAG_CHUNK_TOKENS", "500"))
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "50"))
