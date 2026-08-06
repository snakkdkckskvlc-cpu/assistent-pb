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

# --- Домены корпуса ---
# Нормативка РФ (ФЗ, ГК, СП, ГОСТ) и документы заказчика хранятся в РАЗНЫХ
# коллекциях: у них разный статус. Ссылаться на СТО НЛМК как на норму права
# нельзя, а проверять договор на соответствие требованиям заказчика — нужно;
# в одной выдаче эти источники путались бы.
#
# Домен «pb» указывает на СУЩЕСТВУЮЩЕЕ имя коллекции, а не на новое
# «pb_corpus»: в рабочем индексе лежит legal_corpus на 3334 чанка, и
# переименование осиротило бы его молча — ретривер вернул бы пустоту без
# единой ошибки. Переименовать можно, но только вместе с переиндексацией:
# задайте RAG_COLLECTION=pb_corpus и прогоните index_corpus.py --reset.
NLMK_COLLECTION_NAME = os.environ.get("RAG_NLMK_COLLECTION", "nlmk_corpus")
# Подпапка документов заказчика внутри корпуса. Индексация ПБ её ПРОПУСКАЕТ
# (см. indexer.build_index) — иначе рекурсивный обход затянул бы документы
# НЛМК в нормативную коллекцию.
NLMK_CORPUS_SUBDIR = "nlmk"

DOMAIN_COLLECTIONS = {"pb": COLLECTION_NAME, "nlmk": NLMK_COLLECTION_NAME}
DEFAULT_DOMAIN = "pb"


def collection_for_domain(domain: str | None = None) -> str:
    """Имя коллекции по домену.

    Неизвестный домен — ошибка, а не тихий возврат к дефолту: опечатка в
    --domain иначе молча проиндексировала бы документы не в ту коллекцию, и
    заметили бы это только по пустой выдаче.
    """
    if not domain:
        return DOMAIN_COLLECTIONS[DEFAULT_DOMAIN]
    try:
        return DOMAIN_COLLECTIONS[domain]
    except KeyError:
        known = ", ".join(sorted(DOMAIN_COLLECTIONS))
        raise ValueError(f"Неизвестный домен корпуса «{domain}». Известные: {known}") from None


def corpus_dir_for_domain(domain: str | None = None) -> Path:
    """Папка с документами домена."""
    if domain == "nlmk":
        return CORPUS_DIR / NLMK_CORPUS_SUBDIR
    return CORPUS_DIR


TOP_K = int(os.environ.get("RAG_TOP_K", "5"))

# Размер чанка в СЛОВАХ (chunk_by_articles принимает max_words), а не в
# токенах, как можно подумать по имени переменной.
#
# Было 500. Модель эмбеддингов multilingual-e5-large обрезает вход на 512
# ТОКЕНАХ, а на нашем корпусе замерено 1,96 токена на слово — то есть чанк в
# 500 слов это около 980 токенов, вдвое больше предела.
#
# Замерено на нашем корпусе нашим же чанкером: из 2400 чанков обрезались 130
# (5,4%), и потеряно 41 967 токенов из 329 571 — 12,7% корпуса не доезжало до
# вектора вовсе. Обрезались при этом ДЛИННЫЕ статьи, то есть самые
# содержательные (ст. 723, 743 ГК).
#
# Обрезка была молчаливой и потому особенно вредной: BM25 видел текст целиком,
# вектор — нет, и расхождение выглядело как «вектор плохо различает близкие
# темы». 250 слов ≈ 490 токенов, влезает с запасом.
CHUNK_TOKENS = int(os.environ.get("RAG_CHUNK_TOKENS", "250"))
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
