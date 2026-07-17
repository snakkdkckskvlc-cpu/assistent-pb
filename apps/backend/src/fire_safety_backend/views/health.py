"""Роутер проверки готовности: Ollama + RAG."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from ..infrastructure import languagetool, llm

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


async def _rag_ready_probe() -> bool:
    try:
        import fire_safety_rag

        # Использует закешированный singleton-ретривер (lru_cache) —
        # без этого каждый health-чек заново грузил бы embedding-модель.
        return await asyncio.to_thread(fire_safety_rag.is_ready)
    except Exception as e:
        log.warning("RAG probe failed: %s", e)
        return False


@router.get("/health")
async def health() -> dict:
    # Три независимые пробы — параллельно, а не суммируя их латентности.
    # ollama/languagetool.healthcheck() уже сами перехватывают httpx.HTTPError
    # и возвращают {"ok": False, ...}; RAG-проба ловит исключения сама
    # (_rag_ready_probe) — ни одна не может провалить gather целиком.
    ollama, rag_ready, lt = await asyncio.gather(
        llm.healthcheck(),
        _rag_ready_probe(),
        languagetool.healthcheck(),
    )

    return {
        "ok": ollama["ok"],
        "ollama": ollama,
        "rag_ready": rag_ready,
        "languagetool_ready": lt["ok"],
    }
