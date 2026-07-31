"""Собрать или проверить integrity.json — манифест контрольных сумм кода.

Приложение при запуске сверяет дерево с этим манифестом и отказывается
стартовать при расхождении (см. fire_safety_desktop/main.py и
fire_safety_backend/infrastructure/integrity.py).

Отсюда важное правило работы: **после любого изменения кода манифест надо
пересобрать и закоммитить вместе с правкой.** Забытый пересбор превратил бы
приложение у сотрудника в кирпич — поэтому `--check` стоит в CI и ломает
сборку раньше, чем это дойдёт до машины заказчика.

Запуск:
    python scripts/build_integrity_manifest.py            # пересобрать
    python scripts/build_integrity_manifest.py --check    # только проверить

Нужен PYTHONPATH=apps/backend/src (как и остальным скриптам).

В выводе только ASCII-маркеры [OK]/[X]/[!]: консоль Windows в cp1251 падает
на эмодзи с UnicodeEncodeError.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "backend" / "src"))

from fire_safety_backend.infrastructure import integrity  # noqa: E402

_MAX_SHOWN = 20


def _report_problems(report: integrity.Report) -> None:
    print(f"[X] {report.reason}")
    problems = report.problems
    for line in problems[:_MAX_SHOWN]:
        print(f"     {line}")
    if len(problems) > _MAX_SHOWN:
        print(f"     ... и ещё {len(problems) - _MAX_SHOWN}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Манифест целостности кода")
    parser.add_argument(
        "--check",
        action="store_true",
        help="проверить, что манифест совпадает с деревом; ничего не записывать",
    )
    args = parser.parse_args()

    root = _REPO_ROOT
    path = integrity.manifest_path(root)

    if args.check:
        report = integrity.verify(root)
        if report.ok:
            print(f"[OK] {report.reason}")
            return 0
        _report_problems(report)
        print()
        print("     Если правка кода намеренная — пересоберите манифест:")
        print("       python scripts/build_integrity_manifest.py")
        return 1

    manifest = integrity.build(root)
    content = integrity.serialize(manifest)
    # Сравниваем ДО записи: одинаковое дерево даёт побайтово одинаковый
    # манифест (даты в нём намеренно нет), поэтому «нечего менять» — это
    # осмысленный и частый результат, и лишний коммит на пустом месте не нужен.
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        print(f"[OK] Манифест уже актуален: файлов {len(manifest['files'])}")
        return 0

    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"[OK] Манифест собран: файлов {len(manifest['files'])} -> {path.name}")
    print("     Не забудьте закоммитить его вместе с правкой кода.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
