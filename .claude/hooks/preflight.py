#!/usr/bin/env python3
"""Прогон всех проверок сборки на ЧИСТОМ дереве — до пуша, а не после.

Зачем появился. За одну сессию сборка краснела трижды, и все три раза причина
была одна: локально проверялось не то, что проверяет CI.

  1. Манифест собрался с незакоммиченными правками соседней сессии — на CI
     хеш файла не сошёлся.
  2. Тест писал учётные записи в НАСТОЯЩУЮ базу и падал со второго прогона; на
     CI база чистая, поэтому первый прогон был зелёным.
  3. Тест импортировал модуль, который лежал в рабочем дереве, но в репозиторий
     ещё не уехал.

Общее у всех трёх — рабочая копия отличается от того, что видит сборка: в ней
есть чужие правки, своя база и незакоммиченные файлы. Проверять надо там, где
их нет.

Что делает: создаёт временный `git worktree` на текущем HEAD (то есть ровно то,
что уйдёт в push) и гоняет там те же шаги, что `.github/workflows/ci.yml`, в
том же порядке. Ничего не чинит и ничего не коммитит.

    ./venv/bin/python .claude/hooks/preflight.py

Не хук в смысле события: вешать его на каждый Bash дорого — полный прогон идёт
минуты. Это команда, которую зовут руками перед push.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

КОРЕНЬ = Path(__file__).resolve().parents[2]
PYTHONPATH = "apps/backend/src:packages/rag/src:apps/desktop/src"

# Ровно шаги CI, в том же порядке. mypy стоит с `|| true` и там, поэтому здесь
# он тоже не блокирующий: расхождение между «локально красно, в сборке зелено»
# стоит дороже, чем пропущенное замечание типов.
ШАГИ: tuple[tuple[str, list[str], bool], ...] = (
    ("Ruff · lint", ["-m", "ruff", "check", "."], True),
    ("Ruff · format check", ["-m", "ruff", "format", "--check", "."], True),
    # --python-version 3.13 нужен ЛОКАЛЬНО: в mypy.ini стоит 3.11 под CI, а в
    # venv питон 3.13, и заглушки numpy оттуда используют синтаксис новее — без
    # флага проверка обрывается на первой же строке чужого .pyi (CLAUDE.md §5).
    (
        "Mypy",
        ["-m", "mypy", "--python-version", "3.13", "apps/backend/src", "packages/rag/src"],
        False,
    ),
    ("Целостность · манифест ↔ дерево", ["scripts/build_integrity_manifest.py", "--check"], True),
    ("Корпус · файлы ↔ git ↔ метаданные", ["scripts/check_corpus.py"], True),
    ("Pytest · smoke + unit", ["-m", "pytest", "-q", "--rootdir=."], True),
)


def _запустить(шаг: str, аргументы: list[str], дерево: Path, python: Path) -> tuple[bool, str]:
    среда = {
        "PYTHONPATH": PYTHONPATH,
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        # Проверка целостности внутри тестов пропускается тем же флагом, что и
        # в CI: там он выставлен в шаге pytest.
        "ASSISTENT_PB_DEV": "1",
        "HOME": str(Path.home()),
    }
    r = subprocess.run(  # noqa: S603 — команды фиксированы списком выше
        [str(python), *аргументы],
        cwd=дерево,
        env=среда,
        capture_output=True,
        text=True,
    )
    вывод = (r.stdout + r.stderr).strip()
    return r.returncode == 0, вывод


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверки сборки на чистом дереве")
    parser.add_argument(
        "--keep", action="store_true", help="не удалять временное дерево (для разбора падения)"
    )
    parser.add_argument(
        "--rev",
        default="HEAD",
        help="что проверять: по умолчанию HEAD — ровно то, что уйдёт в push",
    )
    args = parser.parse_args()

    python = КОРЕНЬ / "venv" / "bin" / "python"
    if not python.exists():
        print("[X] Не найден venv проекта — ./venv/bin/python")
        return 2

    временный = Path(tempfile.mkdtemp(prefix="preflight-"))
    дерево = временный / "tree"
    # --detach: ветку не занимаем, иначе параллельная сессия не сможет
    # переключиться на неё в основном дереве.
    создание = subprocess.run(  # noqa: S603
        ["/usr/bin/git", "worktree", "add", "--detach", str(дерево), args.rev],
        cwd=КОРЕНЬ,
        capture_output=True,
        text=True,
    )
    if создание.returncode != 0:
        print("[X] Не удалось создать временное дерево:\n" + создание.stderr.strip())
        shutil.rmtree(временный, ignore_errors=True)
        return 2

    # Ссылка на venv проекта. Без неё два теста в tests/unit/test_scripts_venv.py
    # падают: они проверяют, что скрипты находят venv в корне дерева, а во
    # временном дереве его нет. Копировать нельзя — это гигабайты; в CI venv
    # создаётся штатно, поэтому там они проходят.
    ссылка = дерево / "venv"
    if not ссылка.exists():
        ссылка.symlink_to(КОРЕНЬ / "venv")

    print(f"Проверяю {args.rev} на чистом дереве: {дерево}\n")
    провалы: list[str] = []
    try:
        for шаг, аргументы, блокирующий in ШАГИ:
            ок, вывод = _запустить(шаг, аргументы, дерево, python)
            метка = "OK " if ок else ("ПРОВАЛ" if блокирующий else "замечания")
            print(f"[{метка}] {шаг}")
            if not ок:
                хвост = "\n".join(вывод.splitlines()[-12:])
                print("        " + хвост.replace("\n", "\n        "))
                if блокирующий:
                    провалы.append(шаг)
    finally:
        if args.keep:
            print(f"\nВременное дерево оставлено: {дерево}")
        else:
            subprocess.run(  # noqa: S603
                ["/usr/bin/git", "worktree", "remove", "--force", str(дерево)],
                cwd=КОРЕНЬ,
                capture_output=True,
            )
            shutil.rmtree(временный, ignore_errors=True)

    if провалы:
        print(f"\n[X] Сборка упадёт на: {', '.join(провалы)}")
        return 1
    print("\n[OK] Всё, что проверяет сборка, проходит. Можно пушить.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
