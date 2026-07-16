"""Клиент к Ollama. Тонкая обёртка над /api/chat с JSON-режимом."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from .. import config

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


# Общий на всё приложение клиент (lifespan-scoped) — избегаем открытия
# нового TCP-соединения к Ollama на каждый вызов. connect-таймаут короткий,
# чтобы "Ollama не запущена" падало за секунды, а не ждало все 15 минут,
# отведённых на долгую генерацию (read-таймаут).
_TIMEOUT = httpx.Timeout(connect=5.0, read=float(config.LLM_TIMEOUT_SEC), write=30.0, pool=5.0)
_client: httpx.AsyncClient | None = None


def startup() -> None:
    global _client
    _client = httpx.AsyncClient(timeout=_TIMEOUT)


async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _get_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("LLM-клиент не запущен — вызовите llm.startup() в lifespan")
    return _client


async def chat(
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    temperature: float | None = None,
    num_ctx: int | None = None,
    num_predict: int | None = None,
) -> str:
    """Синхронный вызов чата. Возвращает строку с ответом модели."""
    options: dict[str, Any] = {
        "temperature": temperature if temperature is not None else config.LLM_TEMPERATURE,
        "num_ctx": num_ctx if num_ctx is not None else config.LLM_NUM_CTX,
    }
    if num_predict is not None:
        options["num_predict"] = num_predict
    payload: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "options": options,
    }
    if json_mode:
        payload["format"] = "json"

    url = f"{config.OLLAMA_HOST}/api/chat"
    log.info("LLM chat → %s (json=%s, chars=%d)", config.LLM_MODEL, json_mode, len(user))
    try:
        r = await _get_client().post(url, json=payload)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise LLMError(f"Ollama request failed: {e}") from e

    data = r.json()
    content = data.get("message", {}).get("content", "")
    if not content:
        raise LLMError(f"Empty response from Ollama: {data}")
    return content


async def chat_json(system: str, user: str, **kwargs) -> dict:
    """Вызов с JSON-режимом + парсинг. Обрабатывает случай, когда модель
    оборачивает JSON в markdown-код-блок."""
    raw = await chat(system, user, json_mode=True, **kwargs)
    return _parse_json_loose(raw)


def _parse_json_loose(text: str) -> dict:
    text = text.strip()
    # Модель иногда всё же оборачивает в ```json ... ```
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Попробовать найти первую '{' и последнюю '}'
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last > first:
            snippet = text[first : last + 1]
            try:
                return json.loads(snippet)
            except json.JSONDecodeError:
                pass
        raise LLMError(f"Модель вернула невалидный JSON: {e}\n---\n{text[:500]}") from e


async def healthcheck() -> dict:
    """Проверка что Ollama запущена и модель доступна."""
    try:
        r = await _get_client().get(f"{config.OLLAMA_HOST}/api/tags", timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"Ollama недоступна: {e}"}
    tags = r.json().get("models", [])
    names = [m.get("name", "") for m in tags]
    model_ok = any(
        config.LLM_MODEL in n or n.startswith(config.LLM_MODEL.split(":")[0]) for n in names
    )
    return {
        "ok": model_ok,
        "model": config.LLM_MODEL,
        "installed": names,
        "warning": None
        if model_ok
        else f"Модель {config.LLM_MODEL} не установлена. Запустите: ollama pull {config.LLM_MODEL}",
    }
