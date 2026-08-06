"""Зависимости локальных пакетов обязаны быть в requirements-runtime.txt.

Как это ломается. bootstrap.ps1 ставит сначала requirements-runtime.txt, а
потом сами пакеты — с флагом `--no-deps`. Значит блок `dependencies` в
apps/*/pyproject.toml и packages/*/pyproject.toml на машину пользователя НЕ
попадает вовсе: он описывает намерение, а ставится только список из
requirements-runtime.txt.

Так и вышло с `rank-bm25`: объявлена в packages/rag/pyproject.toml, в
requirements-runtime.txt её не было, у пользователя пакета не оказалось.
Отказ при этом тихий — импорт обёрнут в try/except, гибридный поиск молча
терял лексическую половину и работал одним вектором. То есть половина
подсистемы, описанной в документации замерами, в бою не работала, и узнать
об этом можно было только по строке в логе.

Тест сравнивает два списка. Он не проверяет ВЕРСИИ: requirements-runtime.txt
намеренно держит их незакреплёнными (см. шапку файла), и это отдельное
решение, а не недосмотр.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_REQUIREMENTS = _ROOT / "requirements-runtime.txt"
_LOCAL_PYPROJECTS = (
    _ROOT / "apps" / "backend" / "pyproject.toml",
    _ROOT / "apps" / "desktop" / "pyproject.toml",
    _ROOT / "packages" / "rag" / "pyproject.toml",
)

# Пакеты этого же репозитория: их ставит сам bootstrap.ps1 из исходников,
# в списке внешних зависимостей им делать нечего.
_LOCAL_PACKAGES = {"fire-safety-backend", "fire-safety-desktop", "fire-safety-rag"}


def _canonical(name: str) -> str:
    """Имя пакета по PEP 503: регистр и разделители не различаются.

    `rank-bm25`, `rank_bm25` и `Rank.BM25` — один и тот же дистрибутив, а
    torch в одном файле может быть записан иначе, чем в другом.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_names(text: str) -> set[str]:
    names: set[str] = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        # Отрезаем экстры и любые спецификаторы версии: uvicorn[standard]==0.32.1
        name = re.split(r"[\[<>=!~;]", line, maxsplit=1)[0].strip()
        if name:
            names.add(_canonical(name))
    return names


@pytest.mark.parametrize("pyproject", _LOCAL_PYPROJECTS, ids=lambda p: p.parent.name)
def test_declared_dependencies_are_installed_by_bootstrap(pyproject: Path) -> None:
    declared = _requirement_names(
        "\n".join(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["dependencies"])
    )
    installed = _requirement_names(_REQUIREMENTS.read_text(encoding="utf-8"))
    missing = declared - installed - _LOCAL_PACKAGES
    assert not missing, (
        f"{pyproject.relative_to(_ROOT)} объявляет {sorted(missing)}, "
        f"но bootstrap.ps1 ставит только requirements-runtime.txt (локальные пакеты — "
        f"с --no-deps). На машине пользователя этих пакетов не будет."
    )


def test_bm25_is_in_runtime_requirements() -> None:
    """Отдельно и по имени — именно на этом пакете дефект и поймали."""
    assert "rank-bm25" in _requirement_names(_REQUIREMENTS.read_text(encoding="utf-8"))
