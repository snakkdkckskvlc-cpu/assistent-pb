"""Desktop-обёртка: запускает FastAPI backend в фоне и открывает нативное окно.

Точка входа для сборки в .app/.exe. Самодостаточна: apps/backend и
packages/rag либо уже установлены в venv (editable install — тогда просто
импортируются), либо добавляются в sys.path вручную (_prepare_sys_path) —
никакой внешний launcher-скрипт для этого не нужен.
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import sys
import threading
import time
from pathlib import Path

APP_NAME = "Ассистент ПБ"
HOST = "127.0.0.1"
DEFAULT_PORT = 8000

log = logging.getLogger(__name__)


def _project_root() -> Path:
    """fire_safety_desktop/main.py → src → fire_safety_desktop → desktop → apps → корень."""
    return Path(__file__).resolve().parent.parent.parent.parent.parent


def _show_fatal_error(message: str) -> None:
    """Запуск идёт через pythonw.exe (без консоли) — при любой необработанной
    ошибке пользователь иначе не увидит вообще ничего: ни окна, ни консоли,
    ни лога. Пишем traceback в файл рядом с проектом и, на Windows, дублируем
    нативным MessageBox — иначе «клик по ярлыку ничего не делает» невозможно
    отличить от «всё зависло» или «просто медленно грузится»."""
    log_path = _project_root() / "desktop_error.log"
    with contextlib.suppress(OSError):
        log_path.write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n{message}\n", encoding="utf-8")

    if sys.platform == "win32":
        import ctypes

        short = message if len(message) < 1000 else message[:1000] + "…"
        ctypes.windll.user32.MessageBoxW(
            0,
            f"{short}\n\nПодробности: {log_path}",
            f"{APP_NAME} — ошибка запуска",
            0x10,  # MB_ICONERROR
        )


def _show_info(message: str) -> None:
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, message, APP_NAME, 0x40)  # MB_ICONINFORMATION


def _set_app_user_model_id() -> None:
    """Таскбар Windows определяет иконку запущенного приложения не по живому
    HICON окна (это лишь заголовок окна), а по AppUserModelID процесса. Без
    явного AUMID Windows считает "приложением" сам pythonw.exe и показывает
    его иконку в таскбаре — даже когда у окна уже стоит своя иконка. Нужно
    выставить это ДО создания окна."""
    if sys.platform != "win32":
        return
    import ctypes

    with contextlib.suppress(Exception):
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "PozhServis.AssistentPB.Desktop"
        )


def _check_update_in_background(root: Path) -> None:
    try:
        from . import updater

        if updater.check_and_apply_update(root):
            _show_info(
                "Доступно обновление. Приложение обновлено до последней версии и сейчас перезапустится."
            )
            _relaunch()
            os._exit(0)  # резкий выход нормален: перезапуск, не сохраняем состояние
    except Exception:
        log.warning("Update check failed", exc_info=True)


def _relaunch() -> None:
    """Перезапускает тем же способом, каким приложение всегда запускается
    (ярлык/start.bat зовут именно `pythonw -m fire_safety_desktop.main`) —
    нужно после автообновления, чтобы подхватить новый код: старые модули
    уже импортированы в память текущего процесса, простой continue их не
    заменит."""
    import subprocess

    no_window = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    subprocess.Popen(
        [sys.executable, "-m", "fire_safety_desktop.main"],
        cwd=str(_project_root()),
        creationflags=no_window,
    )


try:
    import httpx
    import uvicorn
    import webview
except ImportError as e:
    _show_fatal_error(
        f"Не найден модуль: {e.name}\n\n"
        "Установка зависимостей не завершена. Запустите bootstrap.ps1 ещё раз "
        "или выполните вручную:\n"
        "venv\\Scripts\\pip install -e apps\\backend -e apps\\desktop -e packages\\rag"
    )
    sys.exit(1)


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


def _prepare_path() -> None:
    """Добавляет poppler\\Library\\bin в PATH (нужен pdf2image для сканов PDF).

    Раньше это делал внешний start.bat — из-за этого приложение падало на OCR,
    если запущено любым другим способом (ярлык напрямую на pythonw.exe, IDE,
    сборка PyInstaller). Делаем это здесь, чтобы поведение не зависело от
    способа запуска."""
    poppler_bin = _project_root() / "poppler" / "Library" / "bin"
    if poppler_bin.exists():
        os.environ["PATH"] = f"{poppler_bin}{os.pathsep}{os.environ.get('PATH', '')}"


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


def _wait_backend(url: str, timeout: int = 90) -> bool:
    """Ждём, пока backend начнёт отвечать на HTTP.

    Проверяем "/" (отдача статики), а не "/api/health": health-эндпоинт
    дожидается прогрева RAG (загрузка embedding-модели в память), что на
    первом запуске может занять больше времени, чем нужно окну — раньше
    это приводило к ложному "backend не поднялся" при полностью рабочем
    backend'е, который просто ещё не успел прогреть RAG.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/", timeout=2)
            if r.status_code < 500:
                return True
        except Exception:
            pass
        # Шаг опроса заметен на глаз: backend поднимается за ~2 c, и при шаге
        # 0.5 c окно в среднем ждало ещё четверть секунды уже готовый сервер.
        time.sleep(0.15)
    return False


class _Api:
    """Мост JS↔Python для pywebview (в браузере доступен как window.pywebview.api.*).

    Единственная причина существования — скачивание файлов. Встроенный
    webview (WebView2 на Windows) НЕ поддерживает обычный браузерный
    механизм `<a download>`: клик по такой ссылке внутри окна pywebview
    молча ничего не делает, без единой ошибки. save_file() качает файл с
    локального backend (тот же процесс, тот же порт) и показывает
    нативный системный диалог «Сохранить как» — так пользователь реально
    получает файл на диск. См. app.js::downloadFile()."""

    # Единственный маршрут, который вправе запросить мост. Всё остальное —
    # отказ. Причина: аргумент приходит из JS, а в окне отображается вывод
    # модели и текст загруженного документа, то есть данные, которыми
    # управляет автор договора. При склейке `base_url + download_path` строка
    # «@evil.example.com/collect» превращает адрес в
    # `http://127.0.0.1:8000@evil.example.com/collect` — «127.0.0.1:8000»
    # становится именем пользователя, и запрос уходит на ЧУЖОЙ сервер вместе
    # с содержимым договора. Проверка ниже разрывает эту цепочку даже если
    # XSS в интерфейсе всё-таки появится.
    _ALLOWED_DOWNLOAD_PREFIX = "/api/download/"

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url

    @classmethod
    def _is_safe_download_path(cls, path: str) -> bool:
        if not path.startswith(cls._ALLOWED_DOWNLOAD_PREFIX):
            return False
        name = path[len(cls._ALLOWED_DOWNLOAD_PREFIX) :]
        # Дальше — только имя файла: ни каталогов, ни обхода вверх, ни
        # второго URL, ни хитростей с обратным слэшем на Windows.
        return bool(name) and not any(c in name for c in "/\\?#@:")

    def save_file(self, download_path: str, suggested_name: str) -> dict:
        if not self._is_safe_download_path(download_path):
            log.warning("save_file: отклонён небезопасный путь %r", download_path)
            return {"ok": False, "error": "недопустимый путь для скачивания"}
        try:
            r = httpx.get(f"{self._base_url}{download_path}", timeout=30)
            r.raise_for_status()
        except Exception as e:
            return {"ok": False, "error": str(e)}

        window = webview.windows[0]
        result = window.create_file_dialog(webview.SAVE_DIALOG, save_filename=suggested_name)
        if not result:
            return {"ok": False, "error": "cancelled"}
        dest = result if isinstance(result, str) else result[0]
        try:
            Path(dest).write_bytes(r.content)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": dest}


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        _set_app_user_model_id()

        root = _project_root()
        os.chdir(root)
        _prepare_sys_path()
        _prepare_path()

        # В фоне, а не до открытия окна: git fetch — сетевой запрос, в худшем
        # случае секунды простоя интернета. Раньше это тормозило каждый
        # запуск; теперь окно открывается сразу, а обновление (если найдётся)
        # предложит перезапуск отдельным окном, не блокируя старт.
        threading.Thread(target=_check_update_in_background, args=(root,), daemon=True).start()

        port = _pick_port()
        url = f"http://{HOST}:{port}"

        t = threading.Thread(target=_run_backend, args=(port,), daemon=True)
        t.start()

        if not _wait_backend(url):
            _show_fatal_error(
                "Backend не поднялся за 90 секунд.\n"
                "Проверьте, что Ollama запущена (значок ламы в трее)."
            )
            return

        webview.create_window(
            title=APP_NAME,
            url=url,
            width=1280,
            height=860,
            min_size=(900, 600),
            js_api=_Api(url),
        )
        # Без этого на Windows окно/таскбар показывают иконку pythonw.exe —
        # pywebview (winforms-бэкенд) без явного icon= сам достаёт иконку из
        # sys.executable, а это именно pythonw.exe, а не наше приложение.
        icon_path = root / "build" / "icons" / "AppIcon.ico"
        webview.start(icon=str(icon_path) if icon_path.exists() else None)
    except Exception:
        import traceback

        _show_fatal_error(traceback.format_exc())
        raise


if __name__ == "__main__":
    main()
