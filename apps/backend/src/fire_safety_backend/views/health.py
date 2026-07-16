"""Роутер проверки готовности: Ollama + RAG."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from ..infrastructure import llm

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict:
    ollama = await llm.healthcheck()
    rag_ready = False
    try:
        import fire_safety_rag

        # Использует закешированный singleton-ретривер (lru_cache) —
        # без этого каждый health-чек заново грузил бы embedding-модель.
        rag_ready = await asyncio.to_thread(fire_safety_rag.is_ready)
    except Exception as e:
        log.warning("RAG probe failed: %s", e)
    return {"ok": ollama["ok"], "ollama": ollama, "rag_ready": rag_ready}
