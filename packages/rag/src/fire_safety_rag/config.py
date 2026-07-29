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

# --- Гибридный поиск (hybrid_retriever.py) ---
# Отбор точнее, чем у чистого вектора, поэтому фрагментов берём больше.
HYBRID_TOP_K = int(os.environ.get("RAG_HYBRID_TOP_K", "8"))
# Перекос в пользу вектора: лексика точна на совпадающих терминах, но слепа к
# словоформам — стемминга нет, «неустойка» и «неустойки» для BM25 разные слова.
HYBRID_VECTOR_WEIGHT = float(os.environ.get("RAG_HYBRID_VECTOR_WEIGHT", "0.6"))
HYBRID_BM25_WEIGHT = float(os.environ.get("RAG_HYBRID_BM25_WEIGHT", "0.4"))
# Во сколько раз больше кандидатов запрашивать у каждой половины, прежде чем
# объединять и обрезать до top_k.
HYBRID_CANDIDATE_FACTOR = int(os.environ.get("RAG_HYBRID_CANDIDATE_FACTOR", "4"))
# Шкала приведения косинуса к 0..1. Замерено на живом индексе (3334 чанка,
# multilingual-e5-large): заведомо бессмысленный запрос даёт 0.767, реальные
# куски договора — 0.814–0.862, точный запрос по существу — 0.907. То есть
# ВСЁ ниже ~0.79 неотличимо от шума. Фиксированная шкала нужна вместо min-max
# по выдаче: min-max растягивал разброс в девять тысячных на весь диапазон и
# топил настоящие лексические попадания (см. _normalize_vector).
HYBRID_COSINE_FLOOR = float(os.environ.get("RAG_HYBRID_COSINE_FLOOR", "0.75"))
HYBRID_COSINE_CEILING = float(os.environ.get("RAG_HYBRID_COSINE_CEILING", "0.95"))
# С какого абсолютного BM25 лексическое совпадение считается полноценным.
# Замер: осмысленный запрос даёт максимум 37–110, бессмысленный — 0–13.
HYBRID_BM25_SATURATION = float(os.environ.get("RAG_HYBRID_BM25_SATURATION", "30"))
