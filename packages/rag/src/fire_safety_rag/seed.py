"""Заселение локальной ChromaDB готовым публичным корпусом (без пересчёта
эмбеддингов — они уже посчитаны и просто копируются из prebuilt_chroma/).

Иначе каждая свежая установка считает эмбеддинги ~660 чанков нормативки на
CPU без GPU заново — несколько минут впустую, хотя результат идентичен
уже готовому и закоммиченному в git индексу.

Трогает ТОЛЬКО коллекцию legal_corpus и только если она у пользователя ещё
пустая — не задевает никакие другие коллекции (например, letters_history с
приватным архивом писем компании, если он уже проиндексирован локально).
"""

from __future__ import annotations

import gc
import logging
import shutil
import tempfile
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

PREBUILT_DIR = Path(__file__).resolve().parent.parent.parent / "prebuilt_chroma"


def ensure_seeded(chroma_dir: Path | None = None, prebuilt_dir: Path | None = None) -> bool:
    """True, если данные были перенесены. False — если нечего переносить,
    у пользователя уже что-то есть в legal_corpus, или что-то пошло не так
    (тогда просто продолжаем обычную индексацию с нуля — не блокируем)."""
    import chromadb
    from chromadb.utils import embedding_functions

    chroma_dir = chroma_dir or config.CHROMA_DIR
    prebuilt_dir = prebuilt_dir or PREBUILT_DIR
    if not prebuilt_dir.exists():
        return False

    # Та же embedding-функция, что использует indexer.build_index() и
    # Retriever — ChromaDB (с версии, закреплённой в requirements-runtime.txt)
    # запоминает конфигурацию embedding-функции в самой коллекции и не даёт
    # её сменить: get_or_create_collection() без явной embedding_function
    # тут привязал бы дефолтную, и следующий индексатор упал бы на конфликте.
    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=config.EMBED_MODEL,
    )

    try:
        target = chromadb.PersistentClient(path=str(chroma_dir))
        try:
            existing = target.get_collection(config.COLLECTION_NAME)
            if existing.count() > 0:
                return False  # у пользователя уже есть данные — не трогаем
        except Exception:
            pass  # коллекции ещё нет, это ожидаемо на чистой установке

        # Читаем из ВРЕМЕННОЙ КОПИИ, а не из самого prebuilt_chroma.
        #
        # ChromaDB пишет в файл базы даже когда её только открывают на чтение
        # (замерено: git отмечает chroma.sqlite3 изменённым после одного лишь
        # PersistentClient). Эталон лежит в git, а автообновление отказывается
        # работать на изменённой рабочей копии
        # (fire_safety_desktop.updater._is_safe_to_update) — то есть первая же
        # установка НАВСЕГДА отключала обновления, и молча: пользователь видел
        # бы просто, что новые версии не приходят.
        #
        # Копия — 37 МБ один раз за установку, это ничто на фоне индексации.
        #
        # Копия кладётся в data/tmp, а не в системный %TEMP%, по практической
        # причине: ChromaDB держит файлы базы открытыми (mmap), и на Windows
        # каталог сразу после работы не удаляется. В data/tmp остатки подберёт
        # автоочистка (backend services/retention.py), в %TEMP% они лежали бы
        # мусором.
        work_root = chroma_dir.parent / "tmp"
        work_root.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix="prebuilt_", dir=work_root))
        try:
            source_copy = tmp / "chroma"
            shutil.copytree(prebuilt_dir, source_copy)
            source = chromadb.PersistentClient(path=str(source_copy))
            src_collection = source.get_collection(config.COLLECTION_NAME)
            data = src_collection.get(include=["embeddings", "documents", "metadatas"])
        finally:
            # Отпускаем ссылки, чтобы Windows дала удалить файлы; если всё
            # равно не даст — ignore_errors, ошибка удаления временной копии
            # не повод срывать заселение.
            source = src_collection = None
            gc.collect()
            shutil.rmtree(tmp, ignore_errors=True)

        if not data["ids"]:
            return False

        dst_collection = target.get_or_create_collection(
            name=config.COLLECTION_NAME,
            embedding_function=embed_fn,
            metadata={"hnsw:space": "cosine"},
        )
        dst_collection.add(
            ids=data["ids"],
            embeddings=data["embeddings"],
            documents=data["documents"],
            metadatas=data["metadatas"],
        )
        log.info("Заселено из prebuilt_chroma: %d чанков", len(data["ids"]))
        return True
    except Exception:
        log.exception("Не удалось заселить из prebuilt_chroma — продолжаем обычной индексацией")
        return False
