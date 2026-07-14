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
    def __init__(self) -> None:
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
                name=config.COLLECTION_NAME,
                embedding_function=embed_fn,
            )
        except Exception as e:
            log.warning("RAG-коллекция ещё не создана: %s. Юр. анализ будет без ссылок на нормы.", e)
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
            for d, m, dist in zip(docs, metas, distances)
        ]


@lru_cache(maxsize=1)
def _default_retriever() -> Retriever:
    return Retriever()


def retrieve(query: str, top_k: int | None = None) -> list[dict]:
    return _default_retriever().search(query, top_k=top_k)
