"""Запуск скрипта интерпретатором venv, каким бы python его ни позвали.

Зачем. Зависимости приложения (pydantic, chromadb, python-docx) стоят в venv
проекта, а не в системном Python. Команда из документации

    python scripts/add_user.py ivanov

у человека, читающего инструкцию буквально, попадала в СИСТЕМНЫЙ python и
падала с `ModuleNotFoundError: No module named 'pydantic'`. Эта ошибка
указывает на pydantic, хотя виноват выбор интерпретатора, — и что делать
дальше, из неё не следует никак.

Чинить документацией бессмысленно: «не забудьте написать venv\\Scripts\\python»
забудут, и не по своей вине.

### Почему решение принимается по ИМПОРТУ, а не по имени каталога

Наивная проверка «мы не в venv/ → перезапуститься» сломала бы CI: там
окружение поднимает `uv`, каталог называется иначе, и слепой перезапуск либо
не нашёл бы интерпретатор, либо запустил не тот. Поэтому вопрос ставится
иначе: доступны ли зависимости приложения ЗДЕСЬ И СЕЙЧАС. Если да — неважно,
каким интерпретатором нас позвали, всё сработает. Если нет — ищем venv.
"""

from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def use_utf8_console() -> None:
    """Вывод в UTF-8 независимо от кодовой страницы консоли.

    Зачем. На русской Windows консоль отдаёт stdout в cp1251 (или cp866), а
    скрипты печатают «→», «—», «≈» и кавычки-ёлочки. Один такой символ роняет
    скрипт с UnicodeEncodeError — причём ДО того, как он сделает что-нибудь
    полезное: `check_corpus.py` падал на одиннадцатом символе первой строки,
    не дойдя до самой проверки корпуса.

    Ошибка при этом указывает на charmap и кодовую страницу, то есть выглядит
    как поломка окружения, а не как «в строке лишняя стрелка», — и человек,
    запустивший команду из документации, остаётся ни с чем.

    Ставится здесь, а не в каждом скрипте, потому что `_venv` импортируют
    почти все: одно место чинит весь класс. `errors="replace"` — страховка на
    случай экзотического потока: лучше вопросительный знак вместо символа, чем
    падение работающего скрипта.
    """
    for stream in (sys.stdout, sys.stderr):
        # У перенаправленного в pipe потока reconfigure есть, у подменённого
        # тестами StringIO — нет. Отсутствие метода не повод падать.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # Закрытый или экзотический поток переключить нельзя — это не повод
        # ронять скрипт, который ещё не начал работу.
        with contextlib.suppress(ValueError, OSError):
            reconfigure(encoding="utf-8", errors="replace")


# Вызывается при импорте: скрипт мог напечатать что-нибудь ещё до ensure_venv()
# (например, разобрать аргументы и сообщить об ошибке), и к этому моменту
# кодировка уже должна быть безопасной.
use_utf8_console()

# Модуль-индикатор: он есть в requirements-runtime.txt и нужен почти всему
# коду приложения. Его наличие означает «мы в окружении проекта».
_PROBE_MODULE = "pydantic"

# Предохранитель от зацикливания. Обычно хватает того, что после перезапуска
# зависимости находятся, но если venv окажется битым, без этой переменной
# скрипт перезапускал бы сам себя бесконечно — а это хуже понятной ошибки:
# он не падает, он плодит процессы.
_GUARD_ENV = "ASSISTENT_PB_VENV_RELAUNCH"


def dependencies_available(module: str = _PROBE_MODULE) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):  # pragma: no cover — find_spec редко падает
        return False


def venv_python() -> Path | None:
    """Путь к интерпретатору venv проекта или None, если его нет."""
    candidates = (
        "venv/Scripts/python.exe",
        "venv/bin/python",
        ".venv/Scripts/python.exe",
        ".venv/bin/python",
    )
    for rel in candidates:
        candidate = _REPO_ROOT / rel
        if candidate.is_file():
            return candidate
    return None


def ensure_venv(module: str = _PROBE_MODULE) -> None:
    """Перезапускает скрипт интерпретатором venv, если зависимостей тут нет.

    При обычной работе (запуск из venv, `uv run` в CI) не делает ничего и не
    виден вовсе.
    """
    if dependencies_available(module):
        return

    if os.environ.get(_GUARD_ENV):
        # Уже перезапускались, а зависимостей всё нет — значит venv битый.
        # Второй круг ничего не изменит.
        print(f"[X] В окружении проекта нет модуля {module} — venv повреждён.")
        print("    Переустановите зависимости: bootstrap.ps1")
        raise SystemExit(1)

    target = venv_python()
    if target is None:
        print("[X] Не найден venv проекта, а зависимостей в текущем python нет.")
        print(f"    Ожидался: {_REPO_ROOT / 'venv'}")
        print("    Установка не завершена — запустите bootstrap.ps1")
        raise SystemExit(1)

    env = dict(os.environ, **{_GUARD_ENV: "1"})
    # Тот же скрипт и те же аргументы, но правильным интерпретатором. Код
    # возврата пробрасывается, иначе CI и bootstrap.ps1 не увидят, что скрипт
    # на самом деле упал.
    result = subprocess.run([str(target), *sys.argv], env=env, check=False)  # noqa: S603
    raise SystemExit(result.returncode)
