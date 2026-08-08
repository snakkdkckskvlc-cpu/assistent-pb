"""Запрет выхода в интернет: приложению разрешён только localhost.

Зачем. Инструмент обещает пользователю «работает локально, без интернета» и
обрабатывает договоры контрагентов. Но обещание держалось только на том, что
никто не написал сетевой вызов: проверка готовности RAG уходила за
метаданными модели на huggingface.co (несколько десятков запросов на холодный
старт), а вместе с chromadb в venv стоят posthog и opentelemetry, у которых
телеметрия по умолчанию включена. Ни одно из этих обращений не нужно для
работы и ни одно не было видно пользователю.

Что разрешено: только loopback. Приложению больше ничего и не требуется —
Ollama слушает 127.0.0.1:11434, LanguageTool 127.0.0.1:8081, ChromaDB лежит
файлом на диске.

Чего этот запрет НЕ даёт. Это не песочница: патч стоит внутри процесса, и код,
который сознательно его снимет, выйдет в сеть. Он ловит библиотеки, а не
злоумышленника. Настоящая изоляция — правило файрвола на python.exe, но
автообновление ставит зависимости через `sys.executable -m pip`, то есть тем же
python.exe, и файрвол сломал бы обновления. Подробнее — docs/05-quality/security.md.

Автообновления запрет не касается: `git` и `pip` запускаются отдельными
процессами (см. updater.py::_run), а патч живёт только в памяти нашего.
"""

from __future__ import annotations

import errno
import ipaddress
import logging
import os
import socket
from typing import Any

log = logging.getLogger(__name__)


class NetworkBlocked(OSError):
    """Попытка соединения с адресом вне localhost."""


# Имена, которые считаем локальными. Своё имя машины тоже: разрешать процессу
# узнать, как он сам называется, — не выход наружу, а вот запрет на это ломает
# библиотеки неочевидным образом.
def _local_hostnames() -> frozenset[str]:
    names = {"", "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}
    try:
        own = socket.gethostname()
    except OSError:  # pragma: no cover — gethostname не падает на практике
        own = ""
    if own:
        names.add(own.lower())
        names.add(own.split(".")[0].lower())
    return frozenset(names)


_LOCAL_NAMES = _local_hostnames()

# Сколько разных адресов помнить. Библиотека в цикле переподключений иначе
# съест память списком одинаковых записей.
_MAX_TARGETS = 50


def _is_local(host: object) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes | bytearray):
        host = bytes(host).decode("utf-8", "replace")
    if not isinstance(host, str):
        return False
    # strip("[]") — IPv6 в адресах приходит и в квадратных скобках.
    h = host.strip().strip("[]").lower()
    if h in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(h).is_loopback
    except ValueError:
        return False


_installed = False
_orig_connect: Any = None
_orig_connect_ex: Any = None
_orig_getaddrinfo: Any = None
# {"huggingface.co:443": 12} — с числом попыток, иначе один ретраящийся
# клиент выглядит как двадцать разных утечек.
_blocked: dict[str, int] = {}


def _record(target: str) -> None:
    if target not in _blocked and len(_blocked) >= _MAX_TARGETS:
        return
    _blocked[target] = _blocked.get(target, 0) + 1
    if _blocked[target] == 1:
        # Только первый раз: ретраи иначе забьют лог.
        log.warning("Выход в интернет заблокирован: %s", target)


def _target_name(address: object) -> str:
    if isinstance(address, tuple) and address:
        host = address[0]
        port = address[1] if len(address) > 1 else "?"
        return f"{host}:{port}"
    return str(address)


def _check_address(sock: socket.socket, address: object) -> None:
    """Пропускает loopback, всё остальное отклоняет с записью в список."""
    family = getattr(sock, "family", None)
    if family not in (socket.AF_INET, socket.AF_INET6):
        # AF_UNIX и прочие — не выход в интернет по определению.
        return
    host = address[0] if isinstance(address, tuple) and address else None
    if _is_local(host):
        return
    target = _target_name(address)
    _record(target)
    raise NetworkBlocked(
        f"Приложение работает офлайн: соединение с {target} запрещено. Разрешён только localhost."
    )


def _guarded_connect(self: socket.socket, address: Any) -> Any:
    _check_address(self, address)
    return _orig_connect(self, address)


def _guarded_connect_ex(self: socket.socket, address: Any) -> Any:
    try:
        _check_address(self, address)
    except NetworkBlocked:
        # connect_ex по контракту возвращает код ошибки, а не бросает.
        return errno.EACCES
    return _orig_connect_ex(self, address)


def _guarded_getaddrinfo(host: Any, port: Any, *args: Any, **kwargs: Any) -> Any:
    """Блокируется и разрешение имён, не только соединение.

    Если запрещать только connect, имя всё равно уйдёт на DNS-сервер — то есть
    «куда приложение ходило» утечёт даже при неудачном соединении.
    """
    if not _is_local(host):
        _record(f"{host}:{port} (DNS)")
        raise socket.gaierror(
            socket.EAI_NONAME,
            f"Приложение работает офлайн: разрешение имени {host} запрещено",
        )
    return _orig_getaddrinfo(host, port, *args, **kwargs)


def _set_offline_env() -> None:
    """Просим библиотеки не ходить в сеть саму по себе.

    Не вместо запрета, а вместе с ним: без этих флагов huggingface_hub и
    chromadb честно пытаются соединиться, получают отказ и тратят секунды на
    повторы при каждом старте. Флаги обязаны быть выставлены ДО импорта
    huggingface_hub, иначе он их не прочитает.
    """
    for name, value in (
        ("HF_HUB_OFFLINE", "1"),
        ("TRANSFORMERS_OFFLINE", "1"),
        ("HF_HUB_DISABLE_TELEMETRY", "1"),
        ("ANONYMIZED_TELEMETRY", "False"),  # chromadb → posthog
    ):
        os.environ.setdefault(name, value)


def _allowed_by_env() -> bool:
    return os.environ.get("ALLOW_NETWORK", "").strip().lower() in {"1", "true", "yes", "on"}


def install() -> None:
    """Включает запрет. Идемпотентно.

    Повторный вызов обязан быть пустым: иначе _orig_connect запомнит уже
    пропатченную функцию, патч наложится сам на себя и получится бесконечная
    рекурсия при первом же соединении.
    """
    global _installed, _orig_connect, _orig_connect_ex, _orig_getaddrinfo
    if _installed:
        return
    if _allowed_by_env():
        # Нужно при первой индексации корпуса и при разработке: модель
        # эмбеддингов весит 1.3 ГБ и качается из интернета.
        log.warning("Выход в интернет РАЗРЕШЁН (ALLOW_NETWORK=1)")
        return

    _set_offline_env()
    _orig_connect = socket.socket.connect
    _orig_connect_ex = socket.socket.connect_ex
    _orig_getaddrinfo = socket.getaddrinfo
    # Оба кода нужны: method-assign — про саму подмену метода, assignment —
    # про несовпадение сигнатур (наши обёртки принимают адрес как Any, а стаб
    # объявляет его строго). Без второго кода mypy ругался поверх ignore.
    socket.socket.connect = _guarded_connect  # type: ignore[method-assign, assignment]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign, assignment]
    socket.getaddrinfo = _guarded_getaddrinfo  # type: ignore[assignment]
    _installed = True
    log.info("Выход в интернет запрещён: разрешён только localhost")


def uninstall() -> None:
    """Снимает запрет. Нужно тестам — в рабочем коде вызывать неоткуда."""
    global _installed, _orig_connect, _orig_connect_ex, _orig_getaddrinfo
    if not _installed:
        return
    socket.socket.connect = _orig_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _orig_connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _orig_getaddrinfo  # type: ignore[assignment]
    _orig_connect = _orig_connect_ex = _orig_getaddrinfo = None
    _installed = False
    _blocked.clear()


def is_installed() -> bool:
    return _installed


def blocked() -> dict[str, int]:
    """Куда приложение пыталось выйти. Пустой словарь — тоже результат."""
    return dict(_blocked)


def status() -> dict:
    total = sum(_blocked.values())
    return {
        "mode": "loopback" if _installed else "off",
        "reason": (
            "разрешён только localhost"
            if _installed
            else "разрешено настройкой ALLOW_NETWORK"
            if _allowed_by_env()
            else "запрет не включён"
        ),
        "blocked_attempts": total,
        # Самые частые сверху: если что-то ретраится, это важнее одиночной попытки.
        "blocked_targets": [t for t, _ in sorted(_blocked.items(), key=lambda kv: -kv[1])[:10]],
    }
