"""Клиент к LanguageTool — офлайн-детерминированный первый проход перед LLM.

LanguageTool работает отдельным локальным процессом на вендоренном JDK
(tools/languagetool/). Приложение обращается к нему по HTTP; недоступность
сервера не фатальна — деградируем на чистый LLM-путь.

Почему отдельный процесс, а не встроенная Java-библиотека — LanguageTool
лицензирован LGPL-2.1; вызов по HTTP отдельного процесса не является
линковкой и не тянет копилефт-обязательств на наш код (подробный разбор —
docs/08-references.md).

### Почему приложение теперь само поднимает сервер

Раньше запуск был на человеке, и по факту не запускался никогда: установщик
LanguageTool не ставил вовсе, ярлык на рабочем столе зовёт pythonw напрямую,
и в интерфейсе просто висело «LanguageTool не подключен» без единой подсказки,
что с этим делать. В результате КАЖДАЯ проверка орфографии уходила в модель —
минуты вместо секунд, хотя быстрый детерминированный проход был написан и
готов.

Ollama остаётся неуправляемой (её ставит собственный установщик и она живёт
службой Windows), а у LanguageTool службы нет — поэтому поднимаем сами.
Запускаем только если порт свободен, и гасим при выходе только СВОЙ процесс:
если сервер поднят кем-то другим (например, вручную из start.ps1), мы его не
трогаем.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

from .. import config

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Наш собственный процесс сервера. None — мы его не запускали, значит и
# останавливать не наше дело.
_own_server: subprocess.Popen | None = None

# rule.category.id (см. LanguageTool /v2/check) → наш формат ошибки
# ("орфография"/"пунктуация"), используемый и LLM-промптом spellcheck.txt,
# и таблицей во frontend (spellcheck.html). Проверка сознательно сужена до
# орфографии и пунктуации — категории вроде GRAMMAR/STYLE/LOGIC/EXTEND
# (стилистика, согласование, канцелярит) в _CATEGORY_TO_TYPE не попадают и
# просто отбрасываются в check() ниже.
_CATEGORY_TO_TYPE = {
    "TYPOS": "орфография",
    "PUNCTUATION": "пунктуация",
    "TYPOGRAPHY": "пунктуация",
    "CASING": "пунктуация",
}

_SENTENCE_END_CHARS = {".", "!", "?", "…"}

# Общий на всё приложение клиент (lifespan-scoped) — тот же паттерн, что и
# infrastructure/llm.py, вместо нового TCP-соединения на каждый вызов.
_client: httpx.AsyncClient | None = None


def tools_dir() -> Path:
    return config.PROJECT_DIR / "tools" / "languagetool"


def _port() -> int:
    return urlparse(config.LANGUAGETOOL_HOST).port or 8081


def server_command() -> list[str] | None:
    """Команда запуска сервера или None, если LanguageTool не установлен.

    Каталоги ищутся глобом, а не по зашитой версии: апстрим периодически
    бампает версию в имени распакованной папки (та же логика, что в
    tools/languagetool/start.ps1).
    """
    tools = tools_dir()
    jdk = next(iter(sorted(tools.glob("jdk-*"))), None)
    lt = next(iter(sorted(tools.glob("LanguageTool-*"))), None)
    if jdk is None or lt is None:
        return None
    java = jdk / "bin" / ("java.exe" if sys.platform == "win32" else "java")
    jar = lt / "languagetool-server.jar"
    if not java.exists() or not jar.exists():
        return None
    # dict/ в classpath — собственный словарь проекта (spelling_global.txt) с
    # терминами, которые LanguageTool иначе считает опечатками.
    classpath = os.pathsep.join([str(jar), str(tools / "dict")])
    return [
        str(java),
        "-cp",
        classpath,
        "org.languagetool.server.HTTPServer",
        "--port",
        str(_port()),
    ]


def installed() -> bool:
    return server_command() is not None


def _port_is_free() -> bool:
    """Свободен ли порт. Занят — значит сервер уже кем-то поднят."""
    import socket

    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", _port()))
        except OSError:
            return False
    return True


def _spawn_server() -> None:
    cmd = server_command()
    if cmd is None:
        log.info(
            "LanguageTool не установлен — проверка орфографии пойдёт только через модель. "
            "Установка: tools/languagetool/setup.ps1"
        )
        return
    if not _port_is_free():
        log.info("LanguageTool уже слушает порт %d — второй не запускаем", _port())
        return
    try:
        global _own_server
        _own_server = subprocess.Popen(  # noqa: S603
            cmd,
            cwd=str(tools_dir()),
            # DEVNULL, а не PIPE: сервер пишет в stdout постоянно, и никем не
            # вычитываемый пайп рано или поздно заполнится и подвесит java.
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    except OSError as e:
        log.warning("Не удалось запустить LanguageTool: %s", e)
        return
    log.info("LanguageTool запущен (pid %s), порт %d", _own_server.pid, _port())


def startup() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=config.LANGUAGETOOL_TIMEOUT_SEC)
    if config.LANGUAGETOOL_AUTOSTART:
        # Не ждём готовности: java поднимается порядка 15 секунд, а старт
        # приложения специально доводили до 3.2 с. Пока сервер поднимается,
        # проверка орфографии просто идёт через модель — это штатная
        # деградация, а не сбой.
        _spawn_server()


async def shutdown() -> None:
    global _client, _own_server
    if _client is not None:
        await _client.aclose()
        _client = None
    if _own_server is not None:
        _own_server.terminate()
        try:
            _own_server.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover — java обычно уходит сам
            _own_server.kill()
        _own_server = None


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError(
            "LanguageTool-клиент не запущен — вызовите languagetool.startup() в lifespan"
        )
    return _client


def _category_id(match: dict) -> str:
    return (match.get("rule") or {}).get("category", {}).get("id", "")


def _match_to_error(match: dict) -> dict:
    ctx = match.get("context", {})
    ctx_text = ctx.get("text", "")
    ctx_offset = ctx.get("offset", 0)
    ctx_length = ctx.get("length", 0)
    before = ctx_text[ctx_offset : ctx_offset + ctx_length]

    replacements = match.get("replacements") or []
    after = replacements[0].get("value", "") if replacements else ""

    return {
        "type": _CATEGORY_TO_TYPE[_category_id(match)],
        "before": before,
        "after": after,
        "reason": match.get("message") or match.get("shortMessage") or "",
        "source": "languagetool",
    }


def _is_sentence_start(ctx_text: str, ctx_offset: int) -> bool:
    prefix = ctx_text[:ctx_offset].rstrip()
    return not prefix or prefix[-1] in _SENTENCE_END_CHARS


def _is_proper_noun_false_positive(match: dict) -> bool:
    """Morfologik (движок орфографии LanguageTool) флажит любое незнакомое
    слово с заглавной буквы как опечатку — включая фамилии и названия
    организаций, которые промпт LLM прямо просит не трогать (см.
    resources/prompts/spellcheck.txt). Эвристика: слово с заглавной буквы
    НЕ в начале предложения — почти всегда имя собственное, а не опечатка."""
    if _category_id(match) != "TYPOS":
        return False
    ctx = match.get("context", {})
    ctx_text = ctx.get("text", "")
    ctx_offset = ctx.get("offset", 0)
    ctx_length = ctx.get("length", 0)
    word = ctx_text[ctx_offset : ctx_offset + ctx_length]
    if not word or not word[0].isupper():
        return False
    return not _is_sentence_start(ctx_text, ctx_offset)


async def check(text: str, language: str = "ru-RU") -> list[dict]:
    """Проверяет текст через LanguageTool. Пустой список — сервер недоступен
    или ошибок не найдено; вызывающий код не должен различать эти случаи
    (спелл-чек и так уходит дальше на LLM)."""
    if not text.strip():
        return []
    url = f"{config.LANGUAGETOOL_HOST}/v2/check"
    try:
        r = await _get_client().post(url, data={"text": text, "language": language})
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("LanguageTool недоступен (%s) — пропускаю, идём только на LLM", e)
        return []

    try:
        matches = r.json().get("matches", [])
    except ValueError:
        log.warning("LanguageTool вернул невалидный JSON")
        return []

    return [
        _match_to_error(m)
        for m in matches
        if _category_id(m) in _CATEGORY_TO_TYPE and not _is_proper_noun_false_positive(m)
    ]


async def healthcheck() -> dict:
    try:
        r = await _get_client().get(f"{config.LANGUAGETOOL_HOST}/v2/info", timeout=5)
        r.raise_for_status()
    except httpx.HTTPError:
        return {"ok": False}
    return {"ok": True}
