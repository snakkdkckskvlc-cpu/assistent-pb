"""Ретривер по нормативной базе. Ленивая инициализация ChromaDB.

Если chromadb или sentence-transformers не установлены — работает в no-op режиме:
`is_ready()` возвращает False, `search()` возвращает []. Это позволяет запускать
backend без тяжёлых RAG-зависимостей на dev-машине.
"""

from __future__ import annotations

import logging
import os
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

# Последняя ошибка поиска. Пустая строка — сбоев не было. Хранится модулем, а
# не ретривером: /api/health должен уметь спросить состояние, не создавая
# ретривер заново (это перезагрузка embedding-модели).
_search_failure = ""


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
        res = self._query([_QUERY_PREFIX + query], top_k, where)
        return _to_hits(res, index=0) if res else []

    def search_many(
        self, queries: list[str], top_k: int | None = None, where: dict | None = None
    ) -> list[list[dict]]:
        """Батч-версия search(): один round-trip в ChromaDB на несколько запросов."""
        if not self.is_ready() or not queries:
            return [[] for _ in queries]
        res = self._query([_QUERY_PREFIX + q for q in queries], top_k, where)
        if not res:
            return [[] for _ in queries]
        return [_to_hits(res, index=i) for i in range(len(queries))]

    def _query(self, texts: list[str], top_k: int | None, where: dict | None) -> dict | None:
        """Запрос к ChromaDB. None — база ответила ошибкой.

        Раньше исключение отсюда улетало наверх и убивало ВСЮ задачу: вызов
        retrieve_many в pipelines/legal.py не обёрнут, и повреждённый индекс
        означал не «анализ без ссылок на нормативку», а «анализа нет вовсе».
        Так и случилось: на живой базе любой запрос С ФИЛЬТРОМ отменённых
        редакций падал с «Error executing plan: Error finding id», хотя тот же
        запрос без фильтра отрабатывал.

        Деградация здесь громкая, а не тихая: ошибка пишется в лог и
        запоминается для /api/health, чтобы интерфейс сказал «база неисправна»,
        а не молча выдал разбор договора без единой ссылки на закон.
        """
        global _search_failure
        try:
            res = self._collection.query(
                query_texts=texts,
                n_results=top_k or config.TOP_K,
                where=where if where is not None else _DEFAULT_WHERE,
            )
        except Exception as e:
            _search_failure = f"{type(e).__name__}: {e}"
            log.error("Поиск по нормативной базе не выполнен: %s", e)
            return None
        _search_failure = ""
        return res


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


def search_failure() -> str:
    """Последняя ошибка поиска, пустая строка — сбоев не было.

    Индекс может существовать и считаться готовым, но отвечать ошибкой на
    запрос — именно так и было с повреждённой коллекцией. Без этого признака
    интерфейс писал бы «нормативная база подключена», пока поиск не работает.
    """
    return _search_failure


def embed_model_cached() -> bool:
    """Скачана ли модель эмбеддингов в кеш HuggingFace.

    Нужно для внятной диагностики. Приложению запрещён выход в интернет
    (backend/infrastructure/netguard.py), поэтому модель обязана быть скачана
    заранее — установщиком или scripts/warm_models.py. Если её нет, ретривер
    молча уходит в no-op, и «нормативная база не подключена» выглядит точно
    так же, как «индекс пустой», хотя лечится совсем иначе.

    Проверяется наличие каталога в кеше, а не работоспособность модели:
    это подсказка пользователю, а не средство защиты.
    """
    from pathlib import Path

    slug = "models--" + config.EMBED_MODEL.replace("/", "--")
    roots: list[Path] = []
    if hub_cache := os.environ.get("HF_HUB_CACHE"):
        roots.append(Path(hub_cache))
    if hf_home := os.environ.get("HF_HOME"):
        roots.append(Path(hf_home) / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    # Совсем старая раскладка кеша, без подкаталога hub/.
    roots.append(Path.home() / ".cache" / "huggingface")
    return any((root / slug).is_dir() for root in roots)
