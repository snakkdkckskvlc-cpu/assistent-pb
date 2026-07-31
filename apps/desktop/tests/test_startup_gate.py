"""Блокировка запуска при изменённом коде.

Требование заказчика: если код правил не разработчик, приложение не должно
работать. Проверяется именно поведение калитки, а не сама сверка сумм (она в
apps/backend/tests/unit/test_integrity.py).

Здесь же закрепляется вторая половина требования — что калитка НЕ мешает
работать, когда всё в порядке. Предохранитель, который блокирует запуск на
исправной установке, хуже отсутствующего.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fire_safety_backend.infrastructure import integrity
from fire_safety_desktop import main as desktop_main

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def shown(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Перехватывает окно фатальной ошибки вместо MessageBoxW."""
    captured: list[str] = []
    monkeypatch.setattr(desktop_main, "_show_fatal_error", captured.append)
    monkeypatch.delenv(desktop_main.DEV_BYPASS_ENV, raising=False)
    return captured


def _report(**kwargs) -> integrity.Report:
    defaults = {"ok": False, "reason": "Код изменён: изменено 1"}
    return integrity.Report(**{**defaults, **kwargs})


def test_intact_code_passes(
    monkeypatch: pytest.MonkeyPatch, shown: list[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        integrity,
        "verify",
        lambda root=None: integrity.Report(ok=True, reason="Совпало файлов: 87"),
    )
    assert desktop_main._integrity_gate(tmp_path) is True
    assert shown == [], "на исправной установке окна ошибки быть не должно"


def test_changed_code_blocks_startup(
    monkeypatch: pytest.MonkeyPatch, shown: list[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        integrity,
        "verify",
        lambda root=None: _report(changed=["apps/backend/src/fire_safety_backend/config.py"]),
    )
    assert desktop_main._integrity_gate(tmp_path) is False
    assert len(shown) == 1


def test_message_names_the_changed_file_and_the_way_back(
    monkeypatch: pytest.MonkeyPatch, shown: list[str], tmp_path: Path
) -> None:
    """«Что-то не так» без имени файла и без команды восстановления оставляет
    пользователя с неработающим инструментом и без выхода."""
    monkeypatch.setattr(
        integrity,
        "verify",
        lambda root=None: _report(changed=["apps/backend/src/fire_safety_backend/config.py"]),
    )
    desktop_main._integrity_gate(tmp_path)
    message = shown[0]
    assert "config.py" in message
    assert "git reset --hard origin/main" in message
    assert "build_integrity_manifest.py" in message


def test_missing_and_extra_files_are_reported(
    monkeypatch: pytest.MonkeyPatch, shown: list[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(
        integrity,
        "verify",
        lambda root=None: _report(missing=["scripts/index_corpus.py"], extra=["подсадной.py"]),
    )
    desktop_main._integrity_gate(tmp_path)
    assert "index_corpus.py" in shown[0]
    assert "подсадной.py" in shown[0]


def test_long_problem_list_is_truncated(
    monkeypatch: pytest.MonkeyPatch, shown: list[str], tmp_path: Path
) -> None:
    """Нативное окно Windows не резиновое, а полный список всё равно уходит в
    desktop_error.log."""
    monkeypatch.setattr(
        integrity,
        "verify",
        lambda root=None: _report(changed=[f"файл_{i}.py" for i in range(40)]),
    )
    desktop_main._integrity_gate(tmp_path)
    assert "и ещё" in shown[0]


def test_missing_manifest_also_blocks(
    monkeypatch: pytest.MonkeyPatch, shown: list[str], tmp_path: Path
) -> None:
    """Нет файла сумм — целостность не проверить, значит запускать нельзя."""
    monkeypatch.setattr(
        integrity,
        "verify",
        lambda root=None: integrity.Report(ok=False, reason="Файл integrity.json не найден"),
    )
    assert desktop_main._integrity_gate(tmp_path) is False
    assert len(shown) == 1


def test_backend_also_refuses_to_start(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Калитка обязана срабатывать и на СЕРВЕРНОМ пути.

    На сервере десктопная обёртка не запускается вовсе — ТЗ переезда прямо
    велит её не запускать. Пока проверка стояла только в ней, на сервере она не
    выполнялась бы ни разу, то есть требование «изменённый код не работает» там
    не действовало.
    """
    monkeypatch.delenv(integrity.DEV_BYPASS_ENV, raising=False)
    monkeypatch.setattr(
        integrity,
        "verify",
        lambda root=None: _report(changed=["apps/backend/src/fire_safety_backend/config.py"]),
    )
    problem = integrity.problem_report(tmp_path)
    assert problem is not None
    assert "config.py" in problem
    assert "git reset --hard origin/main" in problem


def test_intact_code_gives_no_problem_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(integrity.DEV_BYPASS_ENV, raising=False)
    monkeypatch.setattr(
        integrity, "verify", lambda root=None: integrity.Report(ok=True, reason="Совпало")
    )
    assert integrity.problem_report(tmp_path) is None


def test_dev_bypass_skips_the_check(
    monkeypatch: pytest.MonkeyPatch, shown: list[str], tmp_path: Path
) -> None:
    """Обход нужен разработке. Он же — обход для того, кто правит код
    осознанно; это записано в docs/05-quality/security.md, а не замаскировано."""
    monkeypatch.setenv(desktop_main.DEV_BYPASS_ENV, "1")

    def _must_not_run(root=None):
        raise AssertionError("при обходе сверка не должна вызываться")

    monkeypatch.setattr(integrity, "verify", _must_not_run)
    assert desktop_main._integrity_gate(tmp_path) is True
    assert shown == []
