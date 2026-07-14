"""Роутер проверки готовности: Ollama + RAG."""
from __future__ import annotations

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
        from fire_safety_rag import Retriever
        rag_ready = Retriever().is_ready()
    except Exception as e:
        log.warning("RAG probe failed: %s", e)
    return {"ok": ollama["ok"], "ollama": ollama, "rag_ready": rag_ready}
