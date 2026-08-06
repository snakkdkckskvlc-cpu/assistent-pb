#!/usr/bin/env python3
"""Индексация корпуса в ChromaDB (законы, СП, ГОСТ, документы заказчика).

В отличие от прямого `python -m fire_safety_rag.indexer`, использует парсеры
backend'а (fire_safety_backend.infrastructure.parsers.extract_text) — поэтому
умеет читать не только .txt, но и .docx, и .pdf (текстовый слой или, если это
скан, через Tesseract OCR). Без этого файла PDF/DOCX-документы в корпусе
молча пропускались индексатором с "расширение не поддерживается".

Запуск (из venv проекта, требует пакетов backend + rag):
    python scripts/index_corpus.py                    # нормативка РФ
    python scripts/index_corpus.py --domain nlmk      # документы заказчика
    python scripts/index_corpus.py --dir D:\\Законы     # своя папка
    python scripts/index_corpus.py --reset            # пересоздать с нуля

Домены пишут в РАЗНЫЕ коллекции: у нормативки РФ и у СТО заказчика разный
статус, и смешивать их в одной выдаче нельзя (см. packages/rag/corpus/nlmk/).

Повторный запуск ничего не задублирует — файлы отслеживаются по хэшу.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Скрипт мог быть запущен системным python — тогда зависимостей приложения
# в нём нет, и первый же импорт упал бы с невнятным ModuleNotFoundError.
# Перезапускаемся интерпретатором venv.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _venv import ensure_venv  # noqa: E402

ensure_venv()

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _rel in ("apps/backend/src", "packages/rag/src"):
    sys.path.insert(0, str(_REPO_ROOT / _rel))

# Индексация обязана идти в тех же условиях, что боевой запуск. Без netguard
# скрипт ходит на huggingface.co за метаданными модели эмбеддингов — видно в
# логе прогона. Приложению это запрещено, и собирать индекс в других условиях
# значит собирать его не для того приложения.
from fire_safety_backend.infrastructure import netguard  # noqa: E402

netguard.install()

log = logging.getLogger("index_corpus")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Индексация корпуса в ChromaDB")
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Папка с документами (по умолчанию — папка домена)",
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

    # Заселение готовым индексом — только для стандартной установки публичной
    # нормативки: без --dir на свою папку, без --reset (он и так пересоздаёт с
    # нуля) и только для домена pb — prebuilt_chroma собран именно из него.
    # Сам build_index ниже не становится no-op: он пересчитает эмбеддинги для
    # документов, которых в готовом индексе нет (например, добавленных руками).
    if args.dir is None and not args.reset and args.domain == "pb":
        from fire_safety_rag.seed import ensure_seeded

        if ensure_seeded():
            print("Публичный корпус заселён из готового индекса (без пересчёта эмбеддингов).")

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
