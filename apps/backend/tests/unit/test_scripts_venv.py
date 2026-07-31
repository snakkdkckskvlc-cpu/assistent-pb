"""Скрипты сами находят venv проекта.

Зачем это появилось. Команда из документации

    python scripts/add_user.py ivanov

у человека, читающего инструкцию буквально, попадала в СИСТЕМНЫЙ python и
падала с `ModuleNotFoundError: No module named 'pydantic'`. Ошибка указывает
на pydantic, хотя виноват выбор интерпретатора, и что делать дальше — из неё
не следует никак. Чинить это документацией бессмысленно: «не забудьте написать
venv\\Scripts\\python» забудут, и не по своей вине.

Два свойства проверяются особенно:

1. Решение принимается по ДОСТУПНОСТИ ЗАВИСИМОСТЕЙ, а не по имени каталога.
   Наивное «мы не в venv/ → перезапуститься» сломало бы CI: там окружение
   поднимает uv, и каталог называется иначе.
2. Зацикливания нет. Скрипт, бесконечно перезапускающий сам себя, хуже
   исходной ошибки: он не падает, он плодит процессы.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import _venv  # noqa: E402

# --- Решение принимается по зависимостям ---


def test_does_nothing_when_dependencies_are_available() -> None:
    """Тесты идут из окружения проекта — перезапускать нечего.

    Это же покрывает CI: там `uv run`, каталог окружения называется иначе, но
    зависимости на месте, и трогать ничего не надо.
    """
    assert _venv.dependencies_available() is True
    _venv.ensure_venv()  # ни SystemExit, ни перезапуска


def test_missing_module_is_detected() -> None:
    assert _venv.dependencies_available("такого-модуля-нет") is False


def test_finds_the_project_venv() -> None:
    found = _venv.venv_python()
    assert found is not None
    assert found.is_file()


# --- Перезапуск ---


@pytest.fixture
def without_dependencies(monkeypatch: pytest.MonkeyPatch):
    """Имитирует запуск системным python: зависимостей приложения нет."""
    monkeypatch.setattr(_venv, "dependencies_available", lambda module=None: False)
    monkeypatch.delenv(_venv._GUARD_ENV, raising=False)


def test_relaunch_passes_arguments_and_exit_code(
    without_dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Перезапуск обязан быть прозрачным: те же аргументы, тот же код возврата.
    Иначе CI и bootstrap.ps1 не увидят, что скрипт на самом деле упал."""
    calls: list[dict] = []

    class _Result:
        returncode = 3

    def _fake_run(cmd, **kwargs):
        calls.append({"cmd": cmd, **kwargs})
        return _Result()

    monkeypatch.setattr(_venv.subprocess, "run", _fake_run)
    monkeypatch.setattr(sys, "argv", ["scripts/add_user.py", "ivanov", "--admin"])

    with pytest.raises(SystemExit) as exc:
        _venv.ensure_venv()

    assert exc.value.code == 3
    assert calls[0]["cmd"][1:] == ["scripts/add_user.py", "ivanov", "--admin"]
    assert calls[0]["cmd"][0].endswith(("python.exe", "python"))
    # Предохранитель взведён, иначе перезапущенный процесс сделает то же самое.
    assert calls[0]["env"][_venv._GUARD_ENV] == "1"


def test_second_round_stops_instead_of_looping(
    without_dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    """venv битый: зависимостей нет даже после перезапуска. Второй круг ничего
    не изменит, а бесконечный перезапуск хуже понятной ошибки."""
    monkeypatch.setenv(_venv._GUARD_ENV, "1")

    def _must_not_run(*args, **kwargs):
        raise AssertionError("перезапуск при взведённом предохранителе")

    monkeypatch.setattr(_venv.subprocess, "run", _must_not_run)

    with pytest.raises(SystemExit) as exc:
        _venv.ensure_venv()
    assert exc.value.code == 1


def test_missing_venv_says_what_to_do(
    without_dependencies, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нет ни зависимостей, ни venv — это «установка не завершена», а не
    отказ импорта."""
    monkeypatch.setattr(_venv, "venv_python", lambda: None)
    with pytest.raises(SystemExit) as exc:
        _venv.ensure_venv()
    assert exc.value.code == 1


# --- Ни один скрипт не забыт ---


def test_every_app_dependent_script_uses_the_guard() -> None:
    """Забыть вызов в новом скрипте — значит вернуть ту же непонятную ошибку.

    Проверяются скрипты, которым нужны зависимости приложения. Импорт может
    быть и внутри функции (так сделано в check_corpus.py и index_corpus.py),
    поэтому ищем по тексту, а не по верхнеуровневым импортам.
    """
    guarded = []
    for path in sorted(_SCRIPTS.glob("*.py")):
        if path.name.startswith("_"):
            continue
        source = path.read_text(encoding="utf-8")
        if "fire_safety" not in source:
            continue
        assert "ensure_venv()" in source, f"{path.name} не зовёт ensure_venv()"
        guarded.append(path.name)
    assert len(guarded) >= 8, f"подозрительно мало скриптов под защитой: {guarded}"
