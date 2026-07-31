"""Приложение само поднимает LanguageTool — иначе быстрой полосы не существует.

История вопроса. Быстрый детерминированный проход орфографии был написан
давно, но не работал ни разу: установщик LanguageTool не ставил, ярлык на
рабочем столе зовёт pythonw напрямую (не start.bat), и в интерфейсе просто
висело «LanguageTool не подключен» без подсказки, что делать. Каждая проверка
орфографии уходила в модель — минуты вместо секунд.

Ключевое требование к этой автоматике — не навредить: чужой уже запущенный
сервер не трогать ни при запуске, ни при остановке, и не задерживать старт
приложения (java поднимается ~15 с, а старт доводили до 3.2 с).
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest
from fire_safety_backend import config
from fire_safety_backend.infrastructure import languagetool


@pytest.fixture(autouse=True)
def _clean():
    languagetool._own_server = None
    yield
    languagetool._own_server = None


@pytest.fixture
def installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Правдоподобная установка: портативный JDK и распакованный релиз."""
    tools = tmp_path / "tools" / "languagetool"
    java = tools / "jdk-17.0.9+9" / "bin" / ("java.exe" if _is_win() else "java")
    java.parent.mkdir(parents=True)
    java.write_text("", encoding="utf-8")
    jar = tools / "LanguageTool-6.4" / "languagetool-server.jar"
    jar.parent.mkdir(parents=True)
    jar.write_text("", encoding="utf-8")
    (tools / "dict").mkdir()
    monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)
    return tools


def _is_win() -> bool:
    import sys

    return sys.platform == "win32"


class _FakePopen:
    """Подменяет запуск java: настоящий сервер в тестах не нужен."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.pid = 4242
        self.terminated = False
        self.killed = False

    def __call__(self, cmd, **kwargs):
        self.calls.append({"cmd": cmd, **kwargs})
        return self

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        return 0

    def kill(self) -> None:  # pragma: no cover — вызывается только по таймауту
        self.killed = True


# --- Поиск установки ---


def test_finds_installation_by_glob(installation: Path) -> None:
    """Версии в именах папок апстрим бампает — зашивать их нельзя."""
    cmd = languagetool.server_command()
    assert cmd is not None
    assert "jdk-17.0.9+9" in cmd[0]
    assert "LanguageTool-6.4" in cmd[2]
    assert cmd[3] == "org.languagetool.server.HTTPServer"


def test_dict_is_on_the_classpath(installation: Path) -> None:
    """Свой словарь проекта — без него термины ПБ считаются опечатками."""
    cmd = languagetool.server_command()
    assert str(installation / "dict") in cmd[2]


def test_not_installed_gives_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)
    assert languagetool.server_command() is None
    assert languagetool.installed() is False


def test_unpacked_but_broken_installation_gives_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Папки есть, а java внутри нет — считаем, что не установлено."""
    tools = tmp_path / "tools" / "languagetool"
    (tools / "jdk-17" / "bin").mkdir(parents=True)
    (tools / "LanguageTool-6.4").mkdir(parents=True)
    monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)
    assert languagetool.server_command() is None


# --- Запуск ---


def test_starts_server_when_port_is_free(
    installation: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakePopen()
    monkeypatch.setattr(subprocess, "Popen", fake)
    monkeypatch.setattr(languagetool, "_port_is_free", lambda: True)

    languagetool._spawn_server()

    assert len(fake.calls) == 1
    assert languagetool._own_server is fake


def test_does_not_start_second_server_on_busy_port(
    installation: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сервер могли поднять вручную или другим экземпляром приложения."""
    fake = _FakePopen()
    monkeypatch.setattr(subprocess, "Popen", fake)
    monkeypatch.setattr(languagetool, "_port_is_free", lambda: False)

    languagetool._spawn_server()

    assert fake.calls == []
    assert languagetool._own_server is None


def test_missing_installation_does_not_crash_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нет LanguageTool — приложение обязано работать, просто медленнее."""
    monkeypatch.setattr(config, "PROJECT_DIR", tmp_path)
    fake = _FakePopen()
    monkeypatch.setattr(subprocess, "Popen", fake)

    languagetool._spawn_server()

    assert fake.calls == []


def test_launch_failure_does_not_crash_startup(
    installation: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*args, **kwargs):
        raise OSError("java не запускается")

    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(languagetool, "_port_is_free", lambda: True)

    languagetool._spawn_server()  # не должно бросить

    assert languagetool._own_server is None


def test_output_goes_to_devnull(installation: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Сервер пишет в stdout постоянно, и невычитываемый пайп подвесил бы java."""
    fake = _FakePopen()
    monkeypatch.setattr(subprocess, "Popen", fake)
    monkeypatch.setattr(languagetool, "_port_is_free", lambda: True)

    languagetool._spawn_server()

    assert fake.calls[0]["stdout"] == subprocess.DEVNULL
    assert fake.calls[0]["stderr"] == subprocess.DEVNULL


def test_autostart_can_be_disabled(installation: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Нужно, когда сервер поднимают отдельно — службой или руками."""
    fake = _FakePopen()
    monkeypatch.setattr(subprocess, "Popen", fake)
    monkeypatch.setattr(languagetool, "_port_is_free", lambda: True)
    monkeypatch.setattr(config, "LANGUAGETOOL_AUTOSTART", False)

    languagetool.startup()
    try:
        assert fake.calls == []
    finally:
        languagetool._client = None


# --- Остановка ---


async def test_stops_only_its_own_server(installation: Path) -> None:
    fake = _FakePopen()
    languagetool._own_server = fake
    languagetool._client = None

    await languagetool.shutdown()

    assert fake.terminated is True
    assert languagetool._own_server is None


async def test_does_not_touch_a_server_it_did_not_start() -> None:
    """Сервер, поднятый вручную, приложение гасить не вправе."""
    languagetool._own_server = None
    languagetool._client = None

    await languagetool.shutdown()  # не должно бросить

    assert languagetool._own_server is None


# --- Определение занятости порта ---


def test_busy_port_is_detected() -> None:
    with socket.socket() as srv:
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        import fire_safety_backend.infrastructure.languagetool as lt_module

        original = lt_module._port
        lt_module._port = lambda: port
        try:
            assert lt_module._port_is_free() is False
        finally:
            lt_module._port = original
