"""Разовый индексатор нормативной базы.

Читает файлы из корпуса, режет на чанки, эмбеддит через sentence-transformers
и кладёт в ChromaDB. По умолчанию индексирует .txt-файлы; для DOCX/PDF передайте
свой `text_reader(path) -> str` — это делает пакет независимым от парсеров backend.

Запуск: `python -m fire_safety_rag.indexer`
Повторный запуск обновит только новые/изменённые файлы (по хэшу содержимого).
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path
from typing import Callable

from . import config

log = logging.getLogger(__name__)

TextReader = Callable[[Path], str]


def _default_text_reader(path: Path) -> str:
    """Читает .txt как есть. Для сложных форматов передайте свой reader."""
    if path.suffix.lower() in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(
        f"Файл {path.name}: расширение {path.suffix} не поддерживается стандартным "
        f"reader. Передайте `text_reader` явно (например, парсеры backend)."
    )


def _chunk_text(text: str, chunk_words: int, overlap: int) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i : i + chunk_words])
        chunks.append(chunk)
        i += chunk_words - overlap
    return [c for c in chunks if c.strip()]


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()[:16]


def build_index(
    corpus_dir: Path | None = None,
    reset: bool = False,
    text_reader: TextReader | None = None,
) -> dict:
    import chromadb
    from chromadb.utils import embedding_functions

    corpus_dir = corpus_dir or config.CORPUS_DIR
    if not corpus_dir.exists():
        raise FileNotFoundError(f"Корпус не найден: {corpus_dir}")

    reader = text_reader or _default_text_reader

    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=config.EMBED_MODEL,
    )
    if reset:
        try:
            client.delete_collection(config.COLLECTION_NAME)
        except Exception:
            pass
    collection = client.get_or_create_collection(
        name=config.COLLECTION_NAME,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

    existing = collection.get(include=["metadatas"])
    indexed_hashes = {m.get("file_hash") for m in existing.get("metadatas", []) if m}

    files = [p for p in corpus_dir.rglob("*") if p.is_file() and not p.name.startswith(".")]
    stats = {"files_total": len(files), "files_indexed": 0, "chunks_added": 0, "skipped": 0}

    for path in files:
        try:
            fh = _file_hash(path)
            if fh in indexed_hashes:
                stats["skipped"] += 1
                log.info("Пропуск (уже проиндексирован): %s", path.name)
                continue

            log.info("Обработка: %s", path.name)
            try:
                text = reader(path)
            except Exception as e:
                log.warning("Пропуск: %s (%s)", path.name, e)
                stats["skipped"] += 1
                continue

            if not text.strip():
                log.warning("Пустой текст: %s", path.name)
                stats["skipped"] += 1
                continue

            chunks = _chunk_text(text, config.CHUNK_TOKENS, config.CHUNK_OVERLAP)
            ids = [f"{fh}_{i}" for i in range(len(chunks))]
            metadatas = [
                {"source": path.name, "chunk_idx": i, "file_hash": fh}
                for i in range(len(chunks))
            ]
            collection.add(documents=chunks, ids=ids, metadatas=metadatas)
            stats["files_indexed"] += 1
            stats["chunks_added"] += len(chunks)
        except Exception as e:
            log.exception("Ошибка при индексации %s: %s", path.name, e)

    log.info("Индексация завершена: %s", stats)
    return stats


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Индексация нормативного корпуса в ChromaDB")
    parser.add_argument("--reset", action="store_true", help="Пересоздать коллекцию с нуля")
    parser.add_argument("--corpus", type=Path, default=None, help="Путь к корпусу")
    args = parser.parse_args()
    stats = build_index(corpus_dir=args.corpus, reset=args.reset)
    print(f"\nГотово: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
