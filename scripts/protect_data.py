"""Разово зашифровать файлы, которые уже лежат в data/ открытым текстом.

Нужно один раз после обновления: приложение шифрует всё НОВОЕ, а документы,
загруженные до появления шифрования, продолжают лежать читаемыми — они просто
доживают до истечения срока хранения. На машине сотрудника это могут быть
настоящие договоры, поэтому дешевле дошифровать их сразу.

Запуск:
    python scripts/protect_data.py --dry-run    # только посмотреть
    python scripts/protect_data.py              # зашифровать

Нужен PYTHONPATH=apps/backend/src;packages/rag/src (как и остальным скриптам).

В выводе только ASCII-маркеры [OK]/[X]/[!]: консоль Windows в cp1251 падает
на эмодзи с UnicodeEncodeError.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "backend" / "src"))

from fire_safety_backend import config  # noqa: E402
from fire_safety_backend.infrastructure import secure_files  # noqa: E402


def _plain_files(directory: Path) -> list[Path]:
    """Файлы, которые ещё не зашифрованы."""
    if not directory.exists():
        return []
    return sorted(
        p
        for p in directory.iterdir()
        if p.is_file()
        and p.suffix != secure_files.STORED_SUFFIX
        and not p.read_bytes()[: len(secure_files.MAGIC)].startswith(secure_files.MAGIC)
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Зашифровать оставшиеся открытые файлы в data/")
    parser.add_argument("--dry-run", action="store_true", help="только показать, ничего не менять")
    args = parser.parse_args()

    status = secure_files.status()
    print(f"Шифрование: {status.mode} ({status.reason})")
    if status.mode != "dpapi":
        print("[X] Шифрование недоступно — дошифровать нечем.")
        if status.broken:
            print("    Проверьте учётную запись Windows (см. docs/05-quality/security.md).")
        return 1

    total = 0
    for label, directory in (("uploads", config.UPLOAD_DIR), ("outputs", config.OUTPUT_DIR)):
        files = _plain_files(directory)
        if not files:
            print(f"[OK] {label}: открытых файлов нет")
            continue
        print(f"[!] {label}: открытых файлов {len(files)}")
        for path in files:
            size_kb = path.stat().st_size / 1024
            if args.dry_run:
                print(f"     - {path.name} ({size_kb:.0f} КБ)")
                continue
            data = path.read_bytes()
            # store() сам удаляет открытый файл после записи .enc — но только
            # ПОСЛЕ успешной записи, так что потерять документ на полпути нельзя.
            stored = secure_files.store(path, data)
            # Убеждаемся, что зашифрованное читается обратно, прежде чем
            # радоваться: иначе можно «защитить» документ в мусор.
            if secure_files.load(path) != data:
                print(f"[X] {path.name}: проверка чтения не прошла — файл оставлен как был")
                return 1
            print(f"     -> {stored.name} ({size_kb:.0f} КБ)")
            total += 1

    if args.dry_run:
        print("\nЭто был --dry-run, ничего не изменилось.")
    elif total:
        print(f"\n[OK] Зашифровано файлов: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
