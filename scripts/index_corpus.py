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
    args = parser.parse_args()

    from fire_safety_backend.infrastructure.parsers import extract_text
    from fire_safety_rag.indexer import build_index

    corpus_dir = args.dir or (
        Path(__file__).resolve().parent.parent / "packages" / "rag" / "corpus"
    )
    if not corpus_dir.exists():
        print(f"Папка не найдена: {corpus_dir}", file=sys.stderr)
        return 1

    # Заселение готовым индексом — только для стандартной установки (без
    # --dir на свою папку) и без --reset (--reset и так пересоздаёт с нуля,
    # заселять его перед этим бессмысленно). Сам build_index ниже — не
    # no-op: он всё равно пересчитает эмбеддинги для документов, которых
    # нет в prebuilt_chroma (например, добавленных пользователем вручную).
    if args.dir is None and not args.reset:
        from fire_safety_rag.seed import ensure_seeded

        if ensure_seeded():
            print("Публичный корпус заселён из готового индекса (без пересчёта эмбеддингов).")

    print(f"Индексирую {corpus_dir} (первый раз может грузить embedding-модель)...")
    stats = build_index(corpus_dir=corpus_dir, reset=args.reset, text_reader=extract_text)
    print(f"Готово: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
