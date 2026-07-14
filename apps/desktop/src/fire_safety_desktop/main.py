"""Desktop-обёртка: запускает FastAPI backend в фоне и открывает нативное окно.

Точка входа для сборки в .app/.exe. Все пути к venv/PYTHONPATH прописаны
на уровне launcher-скриптов (start.bat, launcher-скрипт .app).
"""
from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn
import webview

APP_NAME = "Ассистент ПБ"
HOST = "127.0.0.1"
DEFAULT_PORT = 8000

log = logging.getLogger(__name__)


def _project_root() -> Path:
    """fire_safety_desktop/main.py → src → fire_safety_desktop → desktop → apps → корень."""
    return Path(__file__).resolve().parent.parent.parent.parent.parent


def _prepare_sys_path() -> None:
    """Добавляет apps/backend/src и packages/rag/src в sys.path.

    Позволяет запустить без предварительной установки пакетов через pip.
    В сборках через PyInstaller этот шаг ничего не делает — там пакеты уже в bundle.
    """
    root = _project_root()
    candidates = [
        root / "apps" / "backend" / "src",
        root / "packages" / "rag" / "src",
    ]
    for path in candidates:
        if path.exists() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _pick_port() -> int:
    """Если 8000 занят — берём свободный порт."""
    s = socket.socket()
    try:
        s.bind((HOST, DEFAULT_PORT))
        s.close()
        return DEFAULT_PORT
    except OSError:
        s.close()
        s2 = socket.socket()
        s2.bind((HOST, 0))
        port = s2.getsockname()[1]
        s2.close()
        return port


def _run_backend(port: int) -> None:
    from fire_safety_backend.main import app

    uvicorn.run(app, host=HOST, port=port, log_level="warning")


def _wait_backend(url: str, timeout: int = 30) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/api/health", timeout=2)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    root = _project_root()
    os.chdir(root)
    _prepare_sys_path()

    port = _pick_port()
    url = f"http://{HOST}:{port}"

    t = threading.Thread(target=_run_backend, args=(port,), daemon=True)
    t.start()

    if not _wait_backend(url):
        log.error("Backend не поднялся за 30 сек")
        return

    webview.create_window(
        title=APP_NAME,
        url=url,
        width=1280,
        height=860,
        min_size=(900, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
