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

Запускать можно любым python: скрипт сам найдёт venv проекта.

В выводе только ASCII-маркеры [OK]/[X]/[!]: консоль Windows в cp1251 падает
на эмодзи с UnicodeEncodeError.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_REPO_ROOT / "apps" / "backend" / "src"))

# Скрипт мог быть запущен системным python — тогда зависимостей приложения
# в нём нет, и импорт ниже упал бы с невнятным ModuleNotFoundError.
# Перезапускаемся интерпретатором venv.
from _venv import ensure_venv  # noqa: E402

ensure_venv()

from fire_safety_backend.infrastructure import integrity  # noqa: E402

_MAX_SHOWN = 20


def ignored_by_git(root: Path, names: list[str]) -> list[str] | None:
    """Файлы манифеста, которые git игнорирует. None — git недоступен.

    ### Зачем это здесь

    Манифест собирается по РАБОЧЕМУ ДЕРЕВУ, а проверяется на машине, где дерево
    получено из git. Файл, лежащий на диске у того, кто пересобирал манифест, но
    игнорируемый git, попадает в манифест и не попадает к пользователю — и
    приложение отказывается стартовать у ВСЕХ, кроме автора манифеста.

    Это не гипотеза. Так вышло с letterhead_raw.docx: его намеренно убрали из
    git (вторая копия банковских реквизитов в истории), на диске он остался, и
    следующий пересбор манифеста внёс его обратно. В origin/main приехал
    манифест, с которым приложение не запускается. До этого тем же кончилось
    c3a2830.

    Проверяется именно ИГНОРИРУЕМОСТЬ, а не «нет в git». Новый файл, который
    ещё не успели `git add`, — обычная середина работы, и ругаться на неё
    значило бы приучить пропускать предупреждение. Игнорируемый файл — другое:
    он не попадёт в git никогда.
    """
    if not names:
        return []
    try:
        # Строго байты и -z (разделитель NUL), а НЕ text=True с переводами
        # строк. На Windows текстовый режим превращает \n в \r\n, git получает
        # имя файла с \r на конце, и правило-исключение («!letterhead.docx»)
        # перестаёт с ним совпадать — отслеживаемый файл объявляется
        # игнорируемым. Поймано на живом дереве: предохранитель отчитался о
        # letterhead.docx, который в git есть.
        r = subprocess.run(
            ["git", "check-ignore", "--stdin", "-z"],
            cwd=root,
            input="\0".join(names).encode("utf-8"),
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    # 0 — что-то проигнорировано, 1 — ничего. Остальное (128 «не репозиторий»,
    # git не найден) — проверить нечем, и это не то же самое, что «всё чисто».
    if r.returncode not in (0, 1):
        return None
    return sorted(p for p in r.stdout.decode("utf-8").split("\0") if p)


def _report_ignored(ignored: list[str]) -> None:
    print(f"[X] В манифест попали файлы, которые git игнорирует: {len(ignored)}")
    for name in ignored[:_MAX_SHOWN]:
        print(f"     {name}")
    if len(ignored) > _MAX_SHOWN:
        print(f"     ... и ещё {len(ignored) - _MAX_SHOWN}")
    print()
    print("     На свежей установке этих файлов НЕ БУДЕТ, а манифест их требует —")
    print("     приложение не запустится ни у кого, кроме этой машины.")
    print("     Обычная причина: файл убрали из git, но на диске он остался.")
    print("     Уберите его с диска (или верните в git) и пересоберите манифест.")


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
        if not report.ok:
            _report_problems(report)
            print()
            print("     Если правка кода намеренная — пересоберите манифест:")
            print("       python scripts/build_integrity_manifest.py")
            return 1
        # Сверка с деревом прошла — но на ЭТОЙ машине. Файл, который git
        # игнорирует, есть и в дереве, и в манифесте, поэтому verify() его
        # пропускает, а на свежей установке он окажется «пропавшим».
        ignored = ignored_by_git(root, [p.as_posix() for p in integrity.iter_covered(root)])
        if ignored is None:
            print("[!] git недоступен — не проверить, все ли файлы манифеста дойдут до установки")
        elif ignored:
            _report_ignored(ignored)
            return 1
        print(f"[OK] {report.reason}")
        return 0

    manifest = integrity.build(root)
    ignored = ignored_by_git(root, list(manifest["files"]))
    if ignored is None:
        print("[!] git недоступен — не проверить, все ли файлы манифеста дойдут до установки")
    elif ignored:
        _report_ignored(ignored)
        return 1
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
