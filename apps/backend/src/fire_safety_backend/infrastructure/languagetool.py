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
# ("орфография"/"пунктуация"/"стиль"), используемый и LLM-промптом
# spellcheck.txt, и таблицей во frontend (spellcheck.html).
_CATEGORY_TO_TYPE = {
    "TYPOS": "орфография",
    "PUNCTUATION": "пунктуация",
    "TYPOGRAPHY": "пунктуация",
    "CASING": "пунктуация",
    "GRAMMAR": "стиль",
    "STYLE": "стиль",
    "LOGIC": "стиль",
    "EXTEND": "стиль",
}


def _match_to_error(match: dict) -> dict:
    ctx = match.get("context", {})
    ctx_text = ctx.get("text", "")
    ctx_offset = ctx.get("offset", 0)
    ctx_length = ctx.get("length", 0)
    before = ctx_text[ctx_offset : ctx_offset + ctx_length] or ctx_text

    replacements = match.get("replacements") or []
    after = replacements[0].get("value", "") if replacements else ""

    category_id = (match.get("rule") or {}).get("category", {}).get("id", "")
    error_type = _CATEGORY_TO_TYPE.get(category_id, "стиль")

    return {
        "type": error_type,
        "before": before,
        "after": after,
        "reason": match.get("message") or match.get("shortMessage") or "",
        "source": "languagetool",
    }


async def check(text: str, language: str = "ru-RU") -> list[dict]:
    """Проверяет текст через LanguageTool. Пустой список — сервер недоступен
    или ошибок не найдено; вызывающий код не должен различать эти случаи
    (спелл-чек и так уходит дальше на LLM)."""
    if not text.strip():
        return []
    url = f"{config.LANGUAGETOOL_HOST}/v2/check"
    try:
        async with httpx.AsyncClient(timeout=config.LANGUAGETOOL_TIMEOUT_SEC) as client:
            r = await client.post(url, data={"text": text, "language": language})
            r.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("LanguageTool недоступен (%s) — пропускаю, идём только на LLM", e)
        return []

    try:
        matches = r.json().get("matches", [])
    except ValueError:
        log.warning("LanguageTool вернул невалидный JSON")
        return []

    return [_match_to_error(m) for m in matches]


async def healthcheck() -> dict:
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{config.LANGUAGETOOL_HOST}/v2/info")
            r.raise_for_status()
    except httpx.HTTPError:
        return {"ok": False}
    return {"ok": True}
