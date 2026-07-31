"""Включён ли BitLocker на диске, где лежат данные.

Зачем это в приложении: DPAPI-шифрование файлов (dpapi.py) закрывает только
data/uploads и data/outputs. База data/app.db (адресаты, темы писем,
комментарии к оценкам) и коллекция реальных писем компании в ChromaDB
хранятся открытым текстом — шифровать их из кода означало бы SQLCipher, то
есть нативную зависимость, которая на Windows собирается плохо. Единственное,
что закрывает их разом, — шифрование диска средствами ОС.

Поэтому состояние BitLocker показывается в интерфейсе: если он выключен,
пользователь должен об этом знать, а не считать, что «шифрование включено» из
индикатора рядом относится ко всему сразу.

Честная оговорка: WMI-класс Win32_EncryptableVolume читается только с правами
администратора. Приложение работает под обычным пользователем, поэтому
типичный ответ — "unknown". Это лучше, чем показывать "off" там, где мы просто
не смогли посмотреть.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from .. import config

log = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
_TIMEOUT_SEC = 8

# ProtectionStatus из Win32_EncryptableVolume.
_PROTECTION = {"0": "off", "1": "on", "2": "unknown"}

_cached: str | None = None


def _drive_letter() -> str:
    """Буква диска с данными, например `C:`."""
    return Path(config.DATA_DIR).resolve().drive


def _probe() -> str:
    if sys.platform != "win32":
        return "unknown"
    drive = _drive_letter()
    if not drive:
        return "unknown"

    # -Filter, а не Where-Object: класс перечисляет все тома, и на машине с
    # несколькими дисками нам нужен именно тот, где лежит data/.
    script = (
        "$v = Get-CimInstance -Namespace root/cimv2/security/microsoftvolumeencryption "
        f"-ClassName Win32_EncryptableVolume -Filter \"DriveLetter='{drive}'\" "
        "-ErrorAction Stop; "
        "if ($v) { $v.ProtectionStatus } else { 'none' }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT_SEC,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.info("BitLocker: опрос не удался (%s)", e)
        return "unknown"

    if proc.returncode != 0:
        # Обычно это отказ доступа: класс требует прав администратора.
        log.info("BitLocker: опрос вернул %s (нужны права администратора?)", proc.returncode)
        return "unknown"
    return _PROTECTION.get((proc.stdout or "").strip(), "unknown")


def status() -> str:
    """Одно из "on" / "off" / "unknown"; результат кешируется на процесс.

    Кеш обязателен: /api/health опрашивается интерфейсом постоянно, а каждый
    опрос — это запуск powershell. Состояние BitLocker за сессию не меняется.
    """
    global _cached
    if _cached is None:
        _cached = _probe()
        log.info("BitLocker на %s: %s", _drive_letter() or "?", _cached)
    return _cached
