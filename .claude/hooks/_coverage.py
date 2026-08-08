"""Что именно покрыто манифестом целостности.

Список НЕ дублируется, а читается из самого integrity.py: покрытие уже менялось
(из него сознательно убрали, а потом вернули `templates`), и вторая копия
разошлась бы с оригиналом молча. Разбор через ast, а не импорт: хук обязан
работать любым python без venv и без зависимостей проекта.
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_INTEGRITY = "apps/backend/src/fire_safety_backend/infrastructure/integrity.py"


def _literal(tree: ast.Module, name: str):
    """Значение присваивания верхнего уровня `name = <литерал>`."""
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == name and node.value is not None:
                return ast.literal_eval(node.value)
    raise LookupError(f"{name} не найдено в {_INTEGRITY}")


def load(project_dir: Path) -> tuple[tuple, tuple]:
    """Возвращает (_COVERED_DIRS, _COVERED_FILES) из integrity.py."""
    src = (project_dir / _INTEGRITY).read_text(encoding="utf-8")
    tree = ast.parse(src)
    return _literal(tree, "_COVERED_DIRS"), _literal(tree, "_COVERED_FILES")


def is_covered(rel_path: str, covered_dirs, covered_files) -> bool:
    """Попадает ли путь (относительно корня проекта) под манифест."""
    rel = rel_path.replace("\\", "/")
    if rel in covered_files:
        return True
    for rel_dir, suffixes in covered_dirs:
        if rel.startswith(rel_dir + "/") and rel.endswith(tuple(suffixes)):
            return True
    return False
