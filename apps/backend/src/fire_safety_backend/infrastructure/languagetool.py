"""Клиент к LanguageTool — офлайн-детерминированный первый проход перед LLM.

LanguageTool запускается отдельным локальным процессом (tools/languagetool/
start.sh, на вендоренном JDK), НЕ управляется этим приложением — тот же
паттерн, что и Ollama (см. infrastructure/llm.py): просто HTTP-вызов,
недоступность сервера — не фатальна, деградируем на чистый LLM-путь.

Почему отдельный процесс, а не встроенная Java-библиотека — LanguageTool
лицензирован LGPL-2.1; вызов по HTTP отдельного процесса не является
линковкой и не тянет копилефт-обязательств на наш код (подробный разбор —
references/languagetool-master/README_reference.md).
"""

from __future__ import annotations

import logging

import httpx

from .. import config

log = logging.getLogger(__name__)

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


def startup() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=config.LANGUAGETOOL_TIMEOUT_SEC)


async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


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
