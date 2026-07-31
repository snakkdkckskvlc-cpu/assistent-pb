#!/usr/bin/env python3
"""Индексация нормативного корпуса (законы, СП, ГОСТ и т.п.) в ChromaDB.

В отличие от прямого `python -m fire_safety_rag.indexer`, использует парсеры
backend'а (fire_safety_backend.infrastructure.parsers.extract_text) — поэтому
умеет читать не только .txt, но и .docx, и .pdf (текстовый слой или, если это
скан, через Tesseract OCR). Без этого файла PDF/DOCX-документы в корпусе
молча пропускались индексатором с "расширение не поддерживается".

Запуск (из venv проекта, требует пакетов backend + rag):
    python scripts/index_corpus.py                      # packages/rag/corpus, добавить новое
    python scripts/index_corpus.py --dir D:\\Законы       # своя папка
    python scripts/index_corpus.py --reset                # пересоздать коллекцию с нуля

Повторный запуск ничего не задублирует — файлы отслеживаются по хэшу.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

log = logging.getLogger("index_corpus")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Индексация корпуса нормативки в legal_corpus")
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Папка с документами (по умолчанию packages/rag/corpus)",
    )
    parser.add_argument("--reset", action="store_true", help="Пересоздать коллекцию с нуля")
    parser.add_argument(
        "--domain",
        default="pb",
        choices=["pb", "nlmk"],
        help="pb — нормативка РФ (ФЗ, ГК, СП, ГОСТ); nlmk — документы заказчика из corpus/nlmk",
    )
    args = parser.parse_args()

    from fire_safety_backend.infrastructure.parsers import extract_text
    from fire_safety_rag import config as rag_config
    from fire_safety_rag.indexer import build_index

    corpus_dir = args.dir or rag_config.corpus_dir_for_domain(args.domain)
    if not corpus_dir.exists():
        print(f"Папка не найдена: {corpus_dir}", file=sys.stderr)
        if args.domain == "nlmk":
            print("Документы заказчика качает scripts/fetch_nlmk_docs.py", file=sys.stderr)
        return 1

    collection = rag_config.collection_for_domain(args.domain)
    print(f"Индексирую {corpus_dir} → коллекция «{collection}»")
    print("(первый раз может грузить embedding-модель)")
    stats = build_index(
        corpus_dir=corpus_dir, reset=args.reset, text_reader=extract_text, domain=args.domain
    )
    print(f"Готово: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
