"""PreToolUse на Edit/Write: не пустить в файл символы, которых там быть не должно.

Класс отказов, который не видит ни ruff, ни человек глазами. По CLAUDE.md §4.4
это уже случалось: трижды китайские иероглифы внутри русских слов и один раз
невидимый символ забоя (0x08) вместо `\\b` — из-за него правило орфографии не
срабатывало НИКОГДА, и заметить это чтением кода было нельзя.

Плюс BOM у PowerShell: без него Windows PowerShell 5.1 читает файл как cp1251,
заглавная «Г» (D0 93) превращается в типографскую кавычку и ломает разбор. Так
молча не работали пять скриптов установки (коммит 1ac2377) — отказ проявлялся
только у пользователя на Windows и только тишиной.

Точка — PreToolUse, а не PostToolUse: PostToolUse срабатывает после успешной
записи и отменить её не может («Claude still sees the original output»), то есть
испорченный файл уже был бы на диске.

Приоритет — НОЛЬ ложных срабатываний (иначе хук начнут обходить). Поэтому
проверяется только добавляемый текст, а правила намеренно узкие: смешение
алфавитов ловится лишь внутри одного слова, где оно не бывает случайным.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata

# Иероглифы и кана: в этом проекте их не бывает ни в коде, ни в текстах.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿･-ﾟ]")

# Невидимое: нулевой ширины, неразрывный пробел нулевой ширины, BOM в середине.
_INVISIBLE = {
    "​": "ZERO WIDTH SPACE",
    "‌": "ZERO WIDTH NON-JOINER",
    "‍": "ZERO WIDTH JOINER",
    "⁠": "WORD JOINER",
    "﻿": "ZERO WIDTH NO-BREAK SPACE (BOM)",
    "­": "SOFT HYPHEN",
}

# Слово, где кириллица и латиница стоят вплотную. Дефис и цифры разрешены
# отдельно: «СП-7», «АИ-92», «qwen2.5» ложных срабатываний не дают.
_MIXED_WORD = re.compile(r"\b(?=[A-Za-z]*[А-Яа-яЁё])(?=[А-Яа-яЁё]*[A-Za-z])[A-Za-zА-Яа-яЁё]{2,}\b")


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _problems(text: str, file_path: str) -> list[str]:
    out: list[str] = []

    for m in _CJK.finditer(text):
        ch = m.group()
        out.append(
            f"строка {_line_of(text, m.start())}: иероглиф {ch!r} "
            f"(U+{ord(ch):04X}, {unicodedata.name(ch, 'без имени')})"
        )

    for ch, name in _INVISIBLE.items():
        idx = text.find(ch)
        # BOM в самом начале файла — законный, это как раз то, что нужно .ps1.
        while idx != -1:
            if not (ch == "﻿" and idx == 0):
                out.append(
                    f"строка {_line_of(text, idx)}: невидимый символ U+{ord(ch):04X} ({name})"
                )
                break
            idx = text.find(ch, idx + 1)

    for ch in set(text):
        if unicodedata.category(ch) == "Cc" and ch not in "\n\r\t":
            idx = text.find(ch)
            out.append(
                f"строка {_line_of(text, idx)}: управляющий символ U+{ord(ch):04X} "
                f"— вероятно, escape записан буквально (как 0x08 вместо \\b)"
            )

    for m in _MIXED_WORD.finditer(text):
        word = m.group()
        out.append(
            f"строка {_line_of(text, m.start())}: в слове «{word}» смешаны кириллица и латиница"
        )

    if file_path.lower().endswith(".ps1"):
        has_cyr = re.search(r"[А-Яа-яЁё]", text)
        if has_cyr and not text.startswith("﻿"):
            out.append(
                "файл .ps1 содержит кириллицу и НЕ начинается с BOM — "
                "PowerShell 5.1 прочитает его как cp1251 и сломается (см. 1ac2377)"
            )

    return out


def _added_text(tool_name: str, tool_input: dict) -> str:
    """Только добавляемый текст: старое содержимое не наша забота."""
    if tool_name == "Write":
        return tool_input.get("content") or ""
    if tool_name == "Edit":
        return tool_input.get("new_string") or ""
    if tool_name == "MultiEdit":
        return "\n".join((e or {}).get("new_string") or "" for e in tool_input.get("edits") or [])
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    text = _added_text(tool_name, tool_input)
    if not text:
        return 0

    file_path = tool_input.get("file_path") or ""
    problems = _problems(text, file_path)
    if not problems:
        return 0

    listed = "\n".join(f"  - {p}" for p in problems[:8])
    more = f"\n  …и ещё {len(problems) - 8}" if len(problems) > 8 else ""
    reason = (
        f"В тексте символы, которых в этом проекте быть не должно ({file_path}):\n\n"
        f"{listed}{more}\n\n"
        "Этот класс ошибок не видит ни ruff, ни глаз: иероглиф внутри русского слова\n"
        "и невидимый символ забоя вместо \\b уже попадали в код (CLAUDE.md §4.4),\n"
        "причём правило с забоем не срабатывало никогда и обнаружилось не сразу.\n\n"
        "Перепечатайте фрагмент вручную — обычно это след копирования из внешнего\n"
        "источника. Если символ нужен намеренно, скажите об этом."
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
