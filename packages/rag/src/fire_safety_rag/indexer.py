"""Разовый индексатор нормативной базы.

Читает файлы из корпуса, режет на чанки, эмбеддит через sentence-transformers
и кладёт в ChromaDB. По умолчанию индексирует .txt-файлы; для DOCX/PDF передайте
свой `text_reader(path) -> str` — это делает пакет независимым от парсеров backend.

Запуск: `python -m fire_safety_rag.indexer`
Повторный запуск обновит только новые/изменённые файлы (по хэшу содержимого).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import logging
import re
import sys
from collections.abc import Callable
from pathlib import Path

from . import config
from .chunking import chunk_by_articles, chunk_sentences

log = logging.getLogger(__name__)

TextReader = Callable[[Path], str]

SIDECAR_META_FILENAME = "_meta.json"

# Типы документов со статейной/пунктовой разметкой — их режем по границам
# структурных единиц, а не по количеству слов (см. chunk_by_articles).
_ARTICLE_STRUCTURED_TYPES = {"federal_law", "government_decree", "sp", "gost", "code"}

# Префикс, которого требует multilingual-e5-large для индексируемых фрагментов
# (запросы идут с «query: », см. retriever.py). Модель обучена с этими
# префиксами; без них качество поиска заметно ниже. ВАЖНО: менять префиксы
# можно только вместе с полной переиндексацией — старые векторы, построенные
# без префикса, несопоставимы с новыми запросами.
_PASSAGE_PREFIX = "passage: "


def _default_text_reader(path: Path) -> str:
    """Читает .txt как есть. Для сложных форматов передайте свой reader."""
    if path.suffix.lower() in {".txt", ".md"}:
        return path.read_text(encoding="utf-8", errors="replace")
    raise ValueError(
        f"Файл {path.name}: расширение {path.suffix} не поддерживается стандартным "
        f"reader. Передайте `text_reader` явно (например, парсеры backend)."
    )


def _strip_scraper_boilerplate(text: str) -> str:
    """Срезает навигационное меню сайта-источника из начала документа.

    Тексты СП сняты со сборников законодательства, и в начале файла остаётся
    меню сайта: «Законодательство РФ / Кодексы РФ в действующей редакции /
    АПК РФ / Водный кодекс РФ / ГК РФ часть 2 / ...». Это семантическая
    ловушка: запрос про Гражданский кодекс матчится на меню внутри свода
    правил по пожарной сигнализации, и в контекст уезжает мусор вместо нормы.
    """
    marker = re.search(r"^Кодексы РФ в действующей редакции\s*$", text, re.MULTILINE)
    if not marker:
        return text
    # Меню — это плотный список коротких строк-названий кодексов. Считаем его
    # закончившимся на первой строке, которая на название кодекса не похожа.
    lines = text[marker.end() :].split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped.split()) > 6 or stripped.endswith("."):
            return "\n".join(lines[i:]).strip()
    return text


def _document_header(meta: dict, filename: str) -> str:
    """Строка вида «СП 5.13130.2009. Установки пожарной сигнализации…».

    Приписывается к каждому чанку перед эмбеддингом: постатейная нарезка даёт
    короткие пункты («1.1. Настоящий свод правил разработан…»), в которых нет
    ни одного признака, по которому их можно найти — из текста не понять, о
    каком своде правил речь.
    """
    parts = [str(meta.get("act_number", "")).strip(), str(meta.get("title", "")).strip()]
    header = ". ".join(p for p in parts if p)
    return header or Path(filename).stem.replace("_", " ")


def _with_header(chunk_text: str, header: str) -> str:
    return f"{header}\n{chunk_text}" if header else chunk_text


def _chunk_document(text: str, meta: dict) -> list[dict]:
    """Выбирает стратегию нарезки по типу документа из _meta.json.

    Нормативные акты (ФЗ, ГК, ПП РФ, СП, ГОСТ) режутся по границам статей и
    пунктов — иначе в один чанк попадает до восьми разных статей, и модель
    ссылается не на ту. Всё остальное (письма, документы контрагентов, вывод
    OCR) — прежним sentence-aware чанкером.
    """
    doc_type = str(meta.get("doc_type", "")).strip().lower()
    if doc_type in _ARTICLE_STRUCTURED_TYPES:
        return chunk_by_articles(_strip_scraper_boilerplate(text), config.CHUNK_TOKENS)
    return [
        {"text": c, "article": None, "chapter": None}
        for c in chunk_sentences(text, config.CHUNK_TOKENS, config.CHUNK_OVERLAP)
    ]


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()[:16]


def _load_sidecar_metadata(corpus_dir: Path) -> dict[str, dict]:
    """Необязательный corpus/_meta.json — вручную сопровождаемые метаданные
    на конкретный файл (doc_type, act_number, effective_date и т.п.).

    Так документы можно фильтровать/сортировать по типу и актуальности без
    попытки автоматически распарсить их из текста/имени файла — для
    юридического корпуса точность важнее автоматизации, это заполняется
    руками при добавлении документа. Формат: {"имя_файла.pdf": {...}}.
    """
    meta_path = corpus_dir / SIDECAR_META_FILENAME
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Не удалось прочитать %s: %s", meta_path, e)
        return {}


# Файлы, которые лежат в папке корпуса, но документами не являются. README —
# пояснение для человека; попав в индекс, он находится по запросам как будто
# это требование заказчика («СТО НЛМК — не источник права», «обходить каталог
# скриптом нельзя») и притом без doc_type и title.
_NON_DOCUMENT_NAMES = {SIDECAR_META_FILENAME, "README.md", "readme.md"}


def _domain_files(corpus_dir: Path, domain: str | None) -> list[Path]:
    """Файлы домена. Обход рекурсивный, поэтому подпапки ЧУЖИХ доменов надо
    исключать явно: без этого индексация нормативки затянула бы в свою
    коллекцию документы заказчика из corpus/nlmk и перемешала источники.
    """
    excluded = set()
    if domain in (None, "pb"):
        excluded.add((corpus_dir / config.NLMK_CORPUS_SUBDIR).resolve())
    out: list[Path] = []
    for path in corpus_dir.rglob("*"):
        if not path.is_file() or path.name.startswith(".") or path.name in _NON_DOCUMENT_NAMES:
            continue
        if any(parent in excluded for parent in path.resolve().parents):
            continue
        out.append(path)
    return out


def build_index(
    corpus_dir: Path | None = None,
    reset: bool = False,
    text_reader: TextReader | None = None,
    domain: str | None = None,
) -> dict:
    import chromadb
    from chromadb.utils import embedding_functions

    collection_name = config.collection_for_domain(domain)
    corpus_dir = corpus_dir or config.corpus_dir_for_domain(domain)
    if not corpus_dir.exists():
        raise FileNotFoundError(f"Корпус не найден: {corpus_dir}")

    reader = text_reader or _default_text_reader

    client = chromadb.PersistentClient(path=str(config.CHROMA_DIR))
    embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=config.EMBED_MODEL,
    )
    if reset:
        with contextlib.suppress(Exception):
            client.delete_collection(collection_name)
    collection = client.get_or_create_collection(
        name=collection_name,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

    existing = collection.get(include=["metadatas"])
    indexed_hashes = {m.get("file_hash") for m in existing.get("metadatas", []) if m}

    sidecar_meta = _load_sidecar_metadata(corpus_dir)
    files = _domain_files(corpus_dir, domain)
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

            extra_meta = sidecar_meta.get(path.name, {})
            pieces = _chunk_document(text, extra_meta)
            if not pieces:
                log.warning("Не удалось нарезать: %s", path.name)
                stats["skipped"] += 1
                continue

            # Заголовок акта приписывается к тексту чанка перед эмбеддингом.
            # Без него короткий пункт «1.1. Настоящий свод правил разработан…»
            # не содержит ни одного признака, по которому его найти: неясно,
            # какой это свод правил и о чём он вообще.
            header = _document_header(extra_meta, path.name)
            documents = [_PASSAGE_PREFIX + _with_header(p["text"], header) for p in pieces]
            ids = [f"{fh}_{i}" for i in range(len(pieces))]
            metadatas = []
            for i, piece in enumerate(pieces):
                # status проставляется ВСЕГДА, даже если записи в _meta.json нет.
                # Ретривер отсекает отменённые редакции фильтром {"$ne":
                # "superseded"}; поведение такого фильтра на документах БЕЗ поля
                # зависит от версии ChromaDB (в 0.5.23 они проходят, проверено),
                # и полагаться на это нельзя — иначе однажды после обновления
                # библиотеки половина корпуса молча исчезнет из выдачи.
                meta = {
                    "source": path.name,
                    "chunk_idx": i,
                    "file_hash": fh,
                    "status": "actual",
                    **extra_meta,
                }
                if piece.get("article"):
                    meta["article"] = piece["article"]
                if piece.get("chapter"):
                    meta["chapter"] = piece["chapter"]
                metadatas.append(meta)
            collection.add(documents=documents, ids=ids, metadatas=metadatas)
            chunks = pieces  # для статистики ниже
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
    parser.add_argument(
        "--domain",
        default=config.DEFAULT_DOMAIN,
        choices=sorted(config.DOMAIN_COLLECTIONS),
        help="Какой корпус индексировать: pb — нормативка РФ, nlmk — документы заказчика",
    )
    args = parser.parse_args()
    stats = build_index(corpus_dir=args.corpus, reset=args.reset, domain=args.domain)
    print(f"\nГотово: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
