"""mypy должен ДОХОДИТЬ до нашего кода, а не падать на чужих стабах.

Как это ломалось. В `mypy.ini` стояла цель `python_version = 3.11`, а стабы
numpy используют синтаксис `type`, появившийся в 3.12. mypy падал на первом
же импорте с «errors prevented further checking» — то есть не проверял ни
одной нашей строки. В CI шаг стоял с `|| true` и оставался зелёным, поэтому
отказ был полностью тихим: проверка типов числилась в пайплайне и не
выполнялась.

Тест не требует, чтобы ошибок типов не было — их 45, и они разбираются
постепенно. Он требует ровно одного: чтобы mypy ДОШЁЛ до нашего кода и выдал
осмысленный отчёт.
"""

from __future__ import annotations

import configparser
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[4]
_MYPY_INI = _ROOT / "mypy.ini"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"


def test_mypy_target_is_new_enough_for_numpy_stubs() -> None:
    """Цель ниже 3.12 роняет mypy на стабах numpy.

    numpy — не прямая наша зависимость по коду, но приходит с chromadb и
    sentence-transformers, поэтому его стабы разбираются всегда.
    """
    parser = configparser.ConfigParser()
    parser.read(_MYPY_INI, encoding="utf-8")
    version = parser["mypy"]["python_version"]
    major, minor = (int(part) for part in version.split("."))
    assert (major, minor) >= (3, 12), (
        f"python_version = {version}: под этой целью mypy падает на стабах numpy "
        f"и не проверяет наш код вообще"
    )


def test_ci_fails_when_mypy_cannot_run() -> None:
    """Ошибки типов терпим, неработающий mypy — нет.

    Голый `|| true` уравнивает «нашёл ошибки» и «не смог запуститься», а это
    разные вещи: первое — известный долг, второе — сломанный инструмент,
    который молча ничего не проверяет.
    """
    ci = _CI.read_text(encoding="utf-8")
    assert "errors prevented further checking" in ci, (
        "CI не отличает падение mypy от ошибок типов — значит поломка "
        "инструмента снова станет незаметной"
    )
    mypy_step = ci.split("- name: Mypy", 1)[1].split("- name:", 1)[0]
    assert "exit 1" in mypy_step, "шаг mypy не умеет падать ни при каких условиях"


@pytest.mark.parametrize("marker", ["Success", "Found "])
def test_ci_requires_a_real_mypy_report(marker: str) -> None:
    """Пустой вывод — тоже отказ: значит mypy не дошёл до отчёта."""
    ci = _CI.read_text(encoding="utf-8")
    mypy_step = ci.split("- name: Mypy", 1)[1].split("- name:", 1)[0]
    assert re.search(rf"\^?\(?{re.escape(marker)}", mypy_step), (
        f"CI не проверяет, что в отчёте mypy есть «{marker}»"
    )
