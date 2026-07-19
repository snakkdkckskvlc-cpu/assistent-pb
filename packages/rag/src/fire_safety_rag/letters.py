"""Коллекция реальных писем компании — примеры стиля для генерации.

Отдельная ChromaDB-коллекция (letters_history) рядом с нормативной базой:
одно письмо = один документ (письма короткие, ~1 страница — чанкинг только
размыл бы стиль). Генератор письма (backend, pipelines/letter.py) подтягивает
2 ближайших к наброску письма и отдаёт их модели как образцы стиля.

Наполнение — scripts/index_letters.py (разовый разбор архива писем).
Коллекции нет / chromadb не установлен — retrieve_letters() отдаёт [],
генерация просто идёт без примеров (тот же graceful-паттерн, что и
у нормативного ретривера).
"""

from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from typing import TYPE_CHECKING

from . import config
from .retriever import Retriever

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)

# Письмо длиннее — почти наверняка не письмо, а затесавшийся в архив
# договор/смета; и как образец стиля простыня бесполезна.
_MAX_LETTER_CHARS = 6000


@lru_cache(maxsize=1)
def _letters_retriever() -> Retriever:
    return Retriever(collection_name=config.LETTERS_COLLECTION_NAME)


def retrieve_letters(query: str, top_k: int = 2) -> list[dict]:
    return _letters_retriever().search(query, top_k=top_k)


def letters_ready() -> bool:
    return _letters_retriever().is_ready()


def index_letters(letters: Iterable[tuple[str, str]], reset: bool = False) -> dict:
    """Кладёт письма (имя, текст) в коллекцию. Идемпотентно: ID — хэш текста,
    повторный прогон того же архива ничего не задублирует."""
    import chromadb
    from chromadb.utils import embedding_functions

    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=config.EMBED_MODEL,
    )
    if reset:
        import contextlib

        with contextlib.suppress(Exception):
            client.delete_collection(config.LETTERS_COLLECTION_NAME)
    collection = client.get_or_create_collection(
        name=config.LETTERS_COLLECTION_NAME,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

    existing_ids = set(collection.get(include=[]).get("ids", []))
    stats = {"letters_total": 0, "indexed": 0, "skipped_dup": 0, "skipped_long": 0}

    for name, text in letters:
        stats["letters_total"] += 1
        text = text.strip()
        if len(text) > _MAX_LETTER_CHARS:
            log.info("Пропуск (слишком длинный для письма, %d символов): %s", len(text), name)
            stats["skipped_long"] += 1
            continue
        doc_id = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        if doc_id in existing_ids:
            stats["skipped_dup"] += 1
            continue
        collection.add(documents=[text], ids=[doc_id], metadatas=[{"source": name}])
        existing_ids.add(doc_id)
        stats["indexed"] += 1

    log.info("Индексация писем завершена: %s", stats)
    return stats
