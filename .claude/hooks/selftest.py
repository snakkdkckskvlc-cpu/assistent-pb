"""Проверка предохранителей на способность краснеть.

Правило проекта (HANDOFF.md §6): «Каждый предохранитель проверяется на
способность краснеть: ломаешь его временно и убеждаешься, что падают именно
нужные тесты. Зелёный тест, который не умеет краснеть, ничего не гарантирует.»

Здесь два списка, и оба обязательны: что хук ОБЯЗАН поймать и — не менее
важно — чего он трогать НЕ ДОЛЖЕН. Ложное срабатывание дороже пропуска:
предохранитель, мешающий работать, выключают в тот же день.

Запуск:  python .claude/hooks/selftest.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parent
CJK = "一"  # 一
BACKSPACE = chr(8)  # символ забоя вместо \b
ZWSP = "​"  # zero width space
BOM = "﻿"


def run(hook: str, payload: dict) -> tuple[str, str]:
    p = subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    if p.returncode != 0:
        return "ОШИБКА", p.stderr.strip()[:160]
    if not p.stdout.strip():
        return "пропустил", ""
    try:
        d = json.loads(p.stdout)
        out = d["hookSpecificOutput"]
        return out["permissionDecision"], out["permissionDecisionReason"].split("\n")[0]
    except Exception as exc:
        return "БИТЫЙ JSON", f"{exc}: {p.stdout[:120]}"


def w(path: str, content: str) -> dict:
    return {"tool_name": "Write", "cwd": ".", "tool_input": {"file_path": path, "content": content}}


def e(path: str, new: str) -> dict:
    return {"tool_name": "Edit", "cwd": ".", "tool_input": {"file_path": path, "new_string": new}}


def b(cmd: str) -> dict:
    return {"tool_name": "Bash", "cwd": ".", "tool_input": {"command": cmd}}


MUST_FIRE = [
    ("иероглиф внутри русского слова", "charcheck.py", w("a.py", f"# провер{CJK}ка текста")),
    (
        "забой вместо \\b в регэкспе",
        "charcheck.py",
        w("a.py", f'RE = "{BACKSPACE}слово{BACKSPACE}"'),
    ),
    ("zero-width space", "charcheck.py", w("a.py", f"x = 1{ZWSP}")),
    ("латиница внутри русского слова", "charcheck.py", w("a.py", "# Aссистент запущен")),
    # Подчёркивание раньше сводило проверку на нет: правило стояло на границах
    # слова \b, а `_` — словообразующий символ, и границы после «dней» в
    # «dней_ноль» нет. В этом проекте почти все имена — русский snake_case,
    # то есть предохранитель был слеп к самой вероятной форме ошибки. Найдено
    # живым промахом в doc-flow.html, закрыто переходом на буквенные отрезки.
    (
        "латиница в имени со снейк-кейсом",
        "charcheck.py",
        w("a.py", "const dней_ноль = 1"),
    ),
    ("латиница в конце составного имени", "charcheck.py", w("a.py", "срок_dней = 5")),
    (".ps1 с кириллицей без BOM", "charcheck.py", w("s.ps1", 'Write-Host "Установка"')),
    ("иероглиф в правке Edit", "charcheck.py", e("a.py", f"return {CJK}")),
]

MUST_NOT_FIRE = [
    ("обычный русский комментарий", "charcheck.py", w("a.py", "# Проверка орфографии\nx = 1")),
    ("чистая латиница", "charcheck.py", w("a.py", "model = 'qwen2.5:7b-instruct'")),
    (
        "кириллица и латиница рядом",
        "charcheck.py",
        w("a.py", "# Модель qwen2.5 на CPU, СП 7, АИ-92, ГК РФ"),
    ),
    # Обратная сторона той же правки: чистый русский снейк-кейс и латинские
    # пути рядом с кириллицей ловиться не должны, иначе хук встанет поперёк
    # обычной работы и его отключат.
    ("чистый русский снейк-кейс", "charcheck.py", w("a.py", "дней_у_текущего = 1")),
    ("латинский путь рядом с русским", "charcheck.py", w("a.py", "# см. /api/doc-flow — журнал")),
    ("табы и переводы строк", "charcheck.py", w("a.py", "def f():\n\treturn 1\n")),
    (".ps1 с BOM", "charcheck.py", w("s.ps1", BOM + 'Write-Host "Установка"')),
    (".ps1 без кириллицы", "charcheck.py", w("s.ps1", 'Write-Host "OK"')),
    ("обычная правка Edit", "charcheck.py", e("a.py", "    return 42")),
    ("git status", "commit_guard.py", b("git status")),
    ("git commit --help", "commit_guard.py", b("git commit --help")),
    ("упоминание git commit в echo", "commit_guard.py", b('echo "не забудь git commit"')),
    ("ruff", "commit_guard.py", b("./venv/bin/python -m ruff check .")),
]


def main() -> int:
    failures = 0

    print("=" * 76)
    print("ДОЛЖНЫ СРАБОТАТЬ (если тут «пропустил» — предохранителя нет)")
    print("=" * 76)
    for name, hook, payload in MUST_FIRE:
        decision, why = run(hook, payload)
        ok = decision == "deny"
        failures += 0 if ok else 1
        print(f"  {'OK ' if ok else 'ПРОВАЛ'} [{decision}] {name}")
        if ok:
            print(f"        → {why[:90]}")

    print()
    print("=" * 76)
    print("НЕ ДОЛЖНЫ СРАБОТАТЬ (тут «deny» — ложное срабатывание, чинить в тот же день)")
    print("=" * 76)
    for name, hook, payload in MUST_NOT_FIRE:
        decision, why = run(hook, payload)
        ok = decision == "пропустил"
        failures += 0 if ok else 1
        print(f"  {'OK ' if ok else 'ПРОВАЛ'} [{decision}] {name}")
        if not ok and why:
            print(f"        → {why[:90]}")

    print()
    print(f"ИТОГ: {'все проверки прошли' if failures == 0 else f'провалов: {failures}'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
