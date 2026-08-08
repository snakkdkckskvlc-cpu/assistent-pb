"""PreToolUse на Bash: не дать закоммитить правку рантайма без пересборки манифеста.

Зачем именно это и именно здесь.

Правило «изменил файл из манифеста — пересобери его в том же коммите» записано в
CLAUDE.md §3.2 крупно, с историей двух кирпичей. Замер по этому репозиторию:
из 32 коммитов, тронувших покрытые файлы, 4 манифест не пересобрали (12.5%) —
2743c703, a34e638c, 43908afb, 752efb18. Последний из них через два коммита
породил c3a2830 «пересобрать манифест — приложение не запускалось».

Текст правила существует и всё равно нарушается в каждом восьмом коммите.
Значит нужен исполнитель вне модели.

Почему точка — `git commit`, а не конец хода. Промах случается в момент коммита,
а не в момент правки: пока файл лежит изменённым в рабочем дереве, всё в порядке.
Проверка на Stop пилила бы на каждом ходу посреди незаконченной работы — а
предохранитель, который срабатывает не по делу, начинают обходить (и это первое,
что убивает такие проверки).

Чего НЕ даёт: хук видит только команды, запущенные Claude Code. Коммит, сделанный
руками в терминале, он не поймает — для этого нужен git pre-commit hook, а это
отдельное решение, затрагивающее второго разработчика.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _coverage import is_covered, load  # noqa: E402

# Ловим `git commit`, но не `git commit --help` и не упоминание в строке.
# Регексп намеренно узкий: ложное срабатывание здесь дороже пропуска.
_GIT_COMMIT = re.compile(r"(^|[;&|]\s*)git\s+(-\S+\s+|--\S+\s+)*commit\b")


def _staged(project_dir: Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # нет входа — не наше дело, пропускаем

    command = (payload.get("tool_input") or {}).get("command", "")
    if not _GIT_COMMIT.search(command):
        return 0

    project_dir = Path(payload.get("cwd") or ".").resolve()
    # Корень проекта, а не текущий каталог: коммит могут запускать из подпапки.
    top = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if top.returncode == 0 and top.stdout.strip():
        project_dir = Path(top.stdout.strip())

    try:
        covered_dirs, covered_files = load(project_dir)
    except Exception:
        return 0  # не смогли прочитать покрытие — молчим, а не мешаем работать

    staged = _staged(project_dir)
    if not staged:
        return 0

    # `git commit -a` подхватит и неиндексированные правки — учитываем их тоже,
    # иначе проверка молча пропустит ровно тот случай, ради которого стоит.
    if re.search(r"(^|\s)-(\w*a\w*)\b|--all\b", command):
        tracked = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        staged += [ln.strip() for ln in tracked.stdout.splitlines() if ln.strip()]

    touched = sorted({p for p in staged if is_covered(p, covered_dirs, covered_files)})
    if not touched:
        return 0
    if "integrity.json" in staged:
        return 0

    shown = "\n".join(f"  - {p}" for p in touched[:10])
    more = f"\n  …и ещё {len(touched) - 10}" if len(touched) > 10 else ""
    reason = (
        "Коммит трогает файлы под контролем целостности, а integrity.json не пересобран.\n\n"
        f"{shown}{more}\n\n"
        "Приложение сверяет файлы с манифестом при старте и ОТКАЗЫВАЕТСЯ запускаться при\n"
        "расхождении — на машинах пользователей это уже дважды давало кирпич (c3a2830, fdcdd98),\n"
        "причём калитка целостности стоит до автообновления, и такая машина сама себя не чинит.\n\n"
        "Пересобрать и добавить в ЭТОТ ЖЕ коммит:\n"
        "  ./venv/bin/python scripts/build_integrity_manifest.py && git add integrity.json\n\n"
        "Если правка намеренно не должна попасть в манифест — скажите об этом,\n"
        "и я не буду настаивать."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
