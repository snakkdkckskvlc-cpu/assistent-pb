#!/usr/bin/env python3
"""Разовая индексация архива реальных писем компании в RAG-коллекцию.

Разбирает ZIP (или папку) с рабочими документами, берёт только DOCX из
подпапки с письмами, извлекает текст абзацев (таблицы пропускаются — в них
шапка бланка с реквизитами, для образца стиля это шум) и кладёт по одному
документу на письмо в ChromaDB-коллекцию letters_history
(см. packages/rag/src/fire_safety_rag/letters.py). После этого генерация
письма подтягивает 2 ближайших к наброску реальных письма как образцы стиля.

Запуск (архив в git не попадает — коммерческие данные, путь свой):
    python scripts/index_letters.py --zip ~/Сжать.zip
    python scripts/index_letters.py --dir "D:\\Архив\\Письма" --folder ""
    python scripts/index_letters.py --zip ~/Сжать.zip --reset   # с нуля

Повторный запуск того же архива ничего не задублирует (ID — хэш текста).
Требует установленных пакетов проекта (python-docx, chromadb) — запускать
из venv проекта. Первая индексация грузит embedding-модель (~1.3 ГБ в кэше
HuggingFace после установки RAG).
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import zipfile
from pathlib import Path, PurePosixPath

# Скрипт мог быть запущен системным python — тогда зависимостей приложения
# в нём нет, и первый же импорт упал бы с невнятным ModuleNotFoundError.
# Перезапускаемся интерпретатором venv.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _venv import ensure_venv  # noqa: E402

ensure_venv()


log = logging.getLogger("index_letters")

_MIN_CHARS = 200  # короче — обрезок/пустой бланк, не образец стиля


def _decode_zip_name(info: zipfile.ZipInfo) -> str:
    """Русские имена в ZIP без UTF-8-флага zipfile отдаёт как cp437-кашу.
    В реальных архивах компании байты имён — UTF-8 (макОS/современный Windows);
    перекодируем, при неудаче пробуем cp866 (старые Windows-архиваторы)."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    raw = name.encode("cp437", errors="replace")
    for enc in ("utf-8", "cp866"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def _docx_paragraphs(data: bytes, name: str) -> str | None:
    from docx import Document

    try:
        doc = Document(io.BytesIO(data))
    except Exception as e:
        log.warning("Не открылся как DOCX: %s (%s)", name, e)
        return None
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    return "\n".join(parts)


def _wanted(path_str: str, folder: str) -> bool:
    p = PurePosixPath(path_str.replace("\\", "/"))
    if p.suffix.lower() != ".docx" or p.name.startswith("~$"):
        return False
    return not folder or any(part.casefold() == folder.casefold() for part in p.parts)


def collect_from_zip(zip_path: Path, folder: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = _decode_zip_name(info)
            if not _wanted(name, folder):
                continue
            text = _docx_paragraphs(zf.read(info), name)
            if text and len(text) >= _MIN_CHARS:
                out.append((PurePosixPath(name).name, text))
            else:
                log.info("Пропуск (пусто/коротко): %s", name)
    return out


def collect_from_dir(root: Path, folder: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for path in sorted(root.rglob("*.docx")):
        if not _wanted(str(path.relative_to(root)), folder):
            continue
        text = _docx_paragraphs(path.read_bytes(), path.name)
        if text and len(text) >= _MIN_CHARS:
            out.append((path.name, text))
        else:
            log.info("Пропуск (пусто/коротко): %s", path.name)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Индексация архива писем в letters_history")
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--zip", type=Path, help="ZIP-архив с документами")
    src.add_argument("--dir", type=Path, help="Папка с документами (уже распакованная)")
    parser.add_argument(
        "--folder",
        default="Письма",
        help="Брать только файлы, у которых в пути есть эта папка "
        "(по умолчанию «%(default)s»; пустая строка — брать все DOCX)",
    )
    parser.add_argument("--reset", action="store_true", help="Пересоздать коллекцию с нуля")
    args = parser.parse_args()

    if args.zip:
        if not args.zip.exists():
            print(f"Архив не найден: {args.zip}", file=sys.stderr)
            return 1
        letters = collect_from_zip(args.zip, args.folder)
    else:
        if not args.dir.exists():
            print(f"Папка не найдена: {args.dir}", file=sys.stderr)
            return 1
        letters = collect_from_dir(args.dir, args.folder)

    if not letters:
        print("Не нашлось ни одного подходящего письма (DOCX, ≥200 символов).", file=sys.stderr)
        return 1

    print(f"Собрано писем: {len(letters)}. Индексирую (первый раз может грузить модель)...")
    from fire_safety_rag.letters import index_letters

    stats = index_letters(letters, reset=args.reset)
    print(f"Готово: {stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
