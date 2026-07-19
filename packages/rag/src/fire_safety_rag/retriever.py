"""Ретривер по нормативной базе. Ленивая инициализация ChromaDB.

Если chromadb или sentence-transformers не установлены — работает в no-op режиме:
`is_ready()` возвращает False, `search()` возвращает []. Это позволяет запускать
backend без тяжёлых RAG-зависимостей на dev-машине.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from . import config

log = logging.getLogger(__name__)


class Retriever:
    def __init__(self, collection_name: str | None = None) -> None:
        name = collection_name or config.COLLECTION_NAME
        self._collection = None
        try:
            import chromadb
            from chromadb.utils import embedding_functions
        except ImportError as e:
            log.warning("chromadb/sentence-transformers не установлены: %s. RAG отключён.", e)
            return

        try:
            client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
            embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=config.EMBED_MODEL,
            )
            self._collection = client.get_collection(
                name=name,
                embedding_function=embed_fn,
            )
        except Exception as e:
            log.warning("RAG-коллекция «%s» ещё не создана: %s. Поиск по ней отключён.", name, e)
            self._collection = None

    def is_ready(self) -> bool:
        return self._collection is not None and self._collection.count() > 0

    def search(self, query: str, top_k: int | None = None) -> list[dict]:
        if not self.is_ready():
            return []
        k = top_k or config.TOP_K
        res = self._collection.query(query_texts=[query], n_results=k)
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        distances = res.get("distances", [[]])[0]
        return [
            {"text": d, "source": m.get("source", "?"), "score": 1 - dist}
            for d, m, dist in zip(docs, metas, distances, strict=True)
        ]

    def search_many(self, queries: list[str], top_k: int | None = None) -> list[list[dict]]:
        """Батч-версия search(): один round-trip в ChromaDB на несколько запросов."""
        if not self.is_ready() or not queries:
            return [[] for _ in queries]
        k = top_k or config.TOP_K
        res = self._collection.query(query_texts=queries, n_results=k)
        docs_lists = res.get("documents", [])
        metas_lists = res.get("metadatas", [])
        dist_lists = res.get("distances", [])
        out: list[list[dict]] = []
        for docs, metas, distances in zip(docs_lists, metas_lists, dist_lists, strict=True):
            out.append(
                [
                    {"text": d, "source": m.get("source", "?"), "score": 1 - dist}
                    for d, m, dist in zip(docs, metas, distances, strict=True)
                ]
            )
        return out


@lru_cache(maxsize=1)
def _default_retriever() -> Retriever:
    return Retriever()


def retrieve(query: str, top_k: int | None = None) -> list[dict]:
    return _default_retriever().search(query, top_k=top_k)


def retrieve_many(queries: list[str], top_k: int | None = None) -> list[list[dict]]:
    return _default_retriever().search_many(queries, top_k=top_k)


def is_ready() -> bool:
    """Готовность RAG без пересоздания ретривера (для /api/health)."""
    return _default_retriever().is_ready()
