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

# multilingual-e5-large обучена с префиксами: индексируемые фрагменты идут как
# «passage: » (см. indexer.py), запросы — как «query: ». Без них качество
# поиска заметно ниже. Менять только вместе с полной переиндексацией: векторы,
# построенные без префикса, несопоставимы с запросами, построенными с ним.
_QUERY_PREFIX = "query: "

# Редакции, отменённые более новыми (в _meta.json помечены status=superseded),
# из выдачи исключаются: ссылаться в анализе договора на утративший силу свод
# правил — хуже, чем не сослаться вовсе.
_DEFAULT_WHERE = {"status": {"$ne": "superseded"}}


def _to_hits(res: dict, index: int) -> list[dict]:
    """Разбирает ответ ChromaDB в наш формат.

    Кроме text/source/score отдаёт метаданные документа (act_number, article,
    doc_type, effective_date, status). Раньше они отбрасывались, и потребитель
    не мог ни отличить действующую редакцию от отменённой, ни проверить, на ту
    ли статью сослалась модель, — хотя в индексе эти поля есть.
    Служебный префикс «passage: » при выдаче снимается: он нужен эмбеддеру,
    но в промпте модели это шум.
    """
    docs = (res.get("documents") or [[]])[index]
    metas = (res.get("metadatas") or [[]])[index]
    distances = (res.get("distances") or [[]])[index]
    hits: list[dict] = []
    for d, m, dist in zip(docs, metas, distances, strict=True):
        meta = m or {}
        text = d or ""
        if text.startswith("passage: "):
            text = text[len("passage: ") :]
        hits.append(
            {
                "text": text,
                "source": meta.get("source", "?"),
                "score": 1 - dist,
                "act_number": meta.get("act_number", ""),
                "article": meta.get("article", ""),
                "chapter": meta.get("chapter", ""),
                "doc_type": meta.get("doc_type", ""),
                "effective_date": meta.get("effective_date", ""),
                "status": meta.get("status", ""),
            }
        )
    return hits


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

    def search(self, query: str, top_k: int | None = None, where: dict | None = None) -> list[dict]:
        if not self.is_ready():
            return []
        k = top_k or config.TOP_K
        res = self._collection.query(
            query_texts=[_QUERY_PREFIX + query],
            n_results=k,
            where=where if where is not None else _DEFAULT_WHERE,
        )
        return _to_hits(res, index=0)

    def search_many(
        self, queries: list[str], top_k: int | None = None, where: dict | None = None
    ) -> list[list[dict]]:
        """Батч-версия search(): один round-trip в ChromaDB на несколько запросов."""
        if not self.is_ready() or not queries:
            return [[] for _ in queries]
        k = top_k or config.TOP_K
        res = self._collection.query(
            query_texts=[_QUERY_PREFIX + q for q in queries],
            n_results=k,
            where=where if where is not None else _DEFAULT_WHERE,
        )
        return [_to_hits(res, index=i) for i in range(len(queries))]


@lru_cache(maxsize=len(config.DOMAIN_COLLECTIONS))
def _retriever_for(collection_name: str) -> Retriever:
    return Retriever(collection_name)


def _default_retriever() -> Retriever:
    return _retriever_for(config.collection_for_domain(None))


def retrieve(query: str, top_k: int | None = None, domain: str | None = None) -> list[dict]:
    return _retriever_for(config.collection_for_domain(domain)).search(query, top_k=top_k)


def retrieve_many(
    queries: list[str],
    top_k: int | None = None,
    where: dict | None = None,
    domain: str | None = None,
) -> list[list[dict]]:
    return _retriever_for(config.collection_for_domain(domain)).search_many(
        queries, top_k=top_k, where=where
    )


def is_ready() -> bool:
    """Готовность RAG без пересоздания ретривера (для /api/health)."""
    return _default_retriever().is_ready()
