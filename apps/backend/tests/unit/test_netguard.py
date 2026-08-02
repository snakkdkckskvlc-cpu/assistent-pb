"""Запрет выхода в интернет.

Приложение обещает пользователю «работает локально, без интернета», но
обещание держалось только на том, что никто не написал сетевой вызов: проверка
готовности RAG уходила на huggingface.co, а у chromadb телеметрия включена по
умолчанию. Здесь проверяется, что обещание теперь обеспечено кодом.

Отдельно проверяется идемпотентность install(): если он запомнит уже
пропатченную функцию как оригинал, патч наложится сам на себя и первое же
соединение уйдёт в бесконечную рекурсию.
"""

from __future__ import annotations

import socket
import threading

import pytest
from fire_safety_backend.infrastructure import netguard


def _own_hostname_resolvable() -> bool:
    """Резолвится ли имя машины САМО ПО СЕБЕ, без установленного запрета."""
    try:
        socket.getaddrinfo(socket.gethostname(), None)
    except OSError:
        return False
    return True


@pytest.fixture
def listener() -> socket.socket:
    """Слушатель на loopback — стенд вместо Ollama."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    threading.Thread(target=_accept_forever, args=(srv,), daemon=True).start()
    yield srv
    srv.close()


def _accept_forever(srv: socket.socket) -> None:
    while True:
        try:
            conn, _ = srv.accept()
            conn.close()
        except OSError:
            return


@pytest.fixture
def guard(monkeypatch: pytest.MonkeyPatch):
    """Запрет включён на время теста, прежнее состояние восстанавливается.

    Восстанавливать обязательно: netguard патчит модуль socket на весь процесс,
    и оставленный включённым запрет утёк бы в остальные тесты. А включён он
    может быть уже до теста — install() вызывается на импорте
    fire_safety_backend.main.
    """
    monkeypatch.delenv("ALLOW_NETWORK", raising=False)
    was_installed = netguard.is_installed()
    netguard.uninstall()
    netguard.install()
    yield netguard
    netguard.uninstall()
    if was_installed:
        netguard.install()


# --- Что разрешено ---


def test_loopback_is_allowed(guard, listener: socket.socket) -> None:
    port = listener.getsockname()[1]
    with socket.socket() as s:
        s.connect(("127.0.0.1", port))  # не должно бросить


def test_localhost_by_name_is_allowed(guard, listener: socket.socket) -> None:
    """Ollama и LanguageTool адресуются и по имени тоже."""
    port = listener.getsockname()[1]
    with socket.socket() as s:
        s.connect(("localhost", port))


def test_localhost_name_resolves(guard) -> None:
    assert socket.getaddrinfo("localhost", 80)


@pytest.mark.skipif(
    not _own_hostname_resolvable(),
    reason=(
        "имя этой машины не резолвится и БЕЗ netguard (типично для macOS с "
        "именем вида *.local) — тест не смог бы отличить запрет от отсутствия "
        "записи в DNS"
    ),
)
def test_own_hostname_resolves(guard) -> None:
    """Узнать своё имя — не выход наружу, а запрет на это ломает библиотеки
    неочевидным образом."""
    assert socket.getaddrinfo(socket.gethostname(), 80)


# --- Что запрещено ---


def test_external_ip_is_blocked(guard) -> None:
    with socket.socket() as s, pytest.raises(netguard.NetworkBlocked):
        s.connect(("93.184.216.34", 443))


def test_external_name_resolution_is_blocked(guard) -> None:
    """Блокировать только connect недостаточно: имя всё равно ушло бы на
    DNS-сервер, то есть «куда мы ходили» утекло бы и при неудачном соединении."""
    with pytest.raises(socket.gaierror):
        socket.getaddrinfo("huggingface.co", 443)


def test_connect_ex_returns_error_instead_of_raising(guard) -> None:
    """connect_ex по контракту возвращает код ошибки, а не бросает — иначе
    библиотека, которая им пользуется, упадёт неожиданным исключением."""
    with socket.socket() as s:
        assert s.connect_ex(("93.184.216.34", 443)) != 0


def test_blocked_attempt_is_recorded(guard) -> None:
    with socket.socket() as s, pytest.raises(netguard.NetworkBlocked):
        s.connect(("93.184.216.34", 443))
    st = guard.status()
    assert st["blocked_attempts"] >= 1
    assert any("93.184.216.34" in t for t in st["blocked_targets"])


def test_retries_of_one_target_are_counted_not_duplicated(guard) -> None:
    """Иначе один ретраящийся клиент выглядит как двадцать разных утечек."""
    for _ in range(5):
        with socket.socket() as s, pytest.raises(netguard.NetworkBlocked):
            s.connect(("93.184.216.34", 443))
    assert guard.blocked()["93.184.216.34:443"] == 5
    assert len(guard.status()["blocked_targets"]) == 1


# --- Поведение самого запрета ---


def test_install_is_idempotent(guard, listener: socket.socket) -> None:
    """Повторный install() не должен наложить патч на патч."""
    guard.install()
    guard.install()
    port = listener.getsockname()[1]
    with socket.socket() as s:
        s.connect(("127.0.0.1", port))  # RecursionError здесь = патч на патче


def test_allow_network_env_disables_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нужно первой индексации корпуса: модель эмбеддингов весит 1.3 ГБ."""
    was_installed = netguard.is_installed()
    netguard.uninstall()
    monkeypatch.setenv("ALLOW_NETWORK", "1")
    try:
        netguard.install()
        assert netguard.is_installed() is False
        assert netguard.status()["mode"] == "off"
    finally:
        monkeypatch.delenv("ALLOW_NETWORK", raising=False)
        if was_installed:
            netguard.install()


def test_offline_env_flags_are_set(guard) -> None:
    """Без них huggingface_hub и chromadb честно пытаются соединиться и тратят
    секунды на обречённые повторы при каждом старте."""
    import os

    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["ANONYMIZED_TELEMETRY"] == "False"


def test_uninstall_restores_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_NETWORK", raising=False)
    was_installed = netguard.is_installed()
    netguard.uninstall()
    original = socket.socket.connect
    netguard.install()
    assert socket.socket.connect is not original
    netguard.uninstall()
    assert socket.socket.connect is original
    if was_installed:
        netguard.install()
