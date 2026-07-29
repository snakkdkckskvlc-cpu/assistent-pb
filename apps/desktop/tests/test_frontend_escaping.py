"""Проверка, что вывод модели не попадает в HTML без экранирования.

Инфраструктуры для JS-тестов в проекте нет, а класс ошибки простой и
статически заметный: поле из ответа модели подставляется в шаблонную строку
и уходит в innerHTML. Так уже было в critLabel() — неизвестное значение
«критичности» возвращалось как есть.

Почему это важно именно здесь: содержимое ответа модели косвенно
контролирует автор анализируемого документа (внедрённая в договор
инструкция), а окно приложения имеет доступ к мосту window.pywebview.api.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_FRONTEND = Path(__file__).resolve().parents[1] / "frontend"

# Поля, которые приходят из ответа модели или из текста документа.
_MODEL_FIELDS = (
    "критичность",
    "цитата_из_договора",
    "в_чём_риск",
    "ссылка_на_норму",
    "предложение_правки",
    "предупреждение",
    "причина",
    "corrected_text",
    "before",
    "after",
    "reason",
)

_INTERPOLATION = re.compile(r"\$\{([^}]*)\}")
# Обёртки, безопасные по построению. Каждая проверяется отдельным тестом
# ниже — иначе этот список превратился бы в способ заглушить проверку.
#   critLabel — экранирует неизвестное значение сам (test_crit_label_...)
#   critClass — возвращает только литералы CSS-классов (test_crit_class_...)
_SAFE = (
    "escapeHtml(",
    "encodeURIComponent(",
    ".map(escapeHtml)",
    "critLabel(",
    "critClass(",
)


def _html_files() -> list[Path]:
    return sorted(_FRONTEND.rglob("*.html")) + sorted(_FRONTEND.glob("*.js"))


def test_frontend_files_are_found() -> None:
    """Страховка от «зелёного» теста, который на самом деле ничего не проверил."""
    assert _html_files(), f"фронтенд не найден: {_FRONTEND}"


@pytest.mark.parametrize("path", _html_files(), ids=lambda p: p.name)
def test_model_output_is_escaped_before_html(path: Path) -> None:
    src = path.read_text(encoding="utf-8")
    unsafe: list[str] = []
    for m in _INTERPOLATION.finditer(src):
        expr = m.group(1)
        if any(s in expr for s in _SAFE):
            continue
        if any(field in expr for field in _MODEL_FIELDS):
            line = src[: m.start()].count("\n") + 1
            unsafe.append(f"{path.name}:{line}  ${{{expr.strip()}}}")
    assert not unsafe, "вывод модели уходит в HTML без escapeHtml:\n" + "\n".join(unsafe)


def test_escape_html_covers_all_dangerous_characters() -> None:
    """Половинчатое экранирование хуже отсутствующего — оно усыпляет."""
    src = (_FRONTEND / "app.js").read_text(encoding="utf-8")
    body = src[src.index("function escapeHtml") : src.index("function escapeHtml") + 400]
    for char in ("&", "<", ">", '"', "'"):
        assert f'"{char}"' in body or f"'{char}'" in body or f"/{char}/g" in body, (
            f"escapeHtml не экранирует {char!r}"
        )


def _function_body(src: str, name: str) -> str:
    body = src[src.index(f"function {name}") :]
    return body[: body.index("}\n")]


def test_crit_label_escapes_unknown_value() -> None:
    """Регрессия: раньше здесь стоял `return crit` — прямой путь из ответа
    модели в innerHTML."""
    src = (_FRONTEND / "views" / "legal.html").read_text(encoding="utf-8")
    body = _function_body(src, "critLabel")
    assert "return escapeHtml(crit" in body, (
        "critLabel обязан экранировать неизвестное значение критичности"
    )


def test_crit_class_returns_only_literals() -> None:
    """critClass подставляется в атрибут class без экранирования.

    Это допустимо ровно пока функция возвращает свои литералы и никогда —
    пришедшее значение.
    """
    src = (_FRONTEND / "views" / "legal.html").read_text(encoding="utf-8")
    body = _function_body(src, "critClass")
    returns = re.findall(r"return\s+([^;]+);", body)
    for r in returns:
        r = r.strip()
        assert r.startswith('"') or r.startswith("'"), (
            f"critClass возвращает не литерал: {r!r} — значение модели попадёт в HTML-атрибут"
        )
