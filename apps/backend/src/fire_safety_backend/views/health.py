"""Роутер проверки готовности: Ollama + RAG."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from .. import config
from ..infrastructure import bitlocker, languagetool, llm, secure_files

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


async def _security_probe() -> dict:
    """Состояние защиты данных на диске.

    BitLocker опрашивается через powershell — это блокирующий запуск процесса,
    поэтому в поток; результат кешируется в bitlocker.status(), так что реально
    процесс запускается один раз за сессию.
    """
    st = secure_files.status()
    try:
        drive_encryption = await asyncio.to_thread(bitlocker.status)
    except Exception as e:  # pragma: no cover — bitlocker.status() ловит сам
        log.warning("BitLocker probe failed: %s", e)
        drive_encryption = "unknown"
    return {
        "encryption": st.mode,
        "encryption_reason": st.reason,
        "encryption_broken": st.broken,
        "retention_days": config.DATA_RETENTION_DAYS,
        "bitlocker": drive_encryption,
    }


@router.get("/health")
async def health() -> dict:
    # Независимые пробы — параллельно, а не суммируя их латентности.
    # ollama/languagetool.healthcheck() уже сами перехватывают httpx.HTTPError
    # и возвращают {"ok": False, ...}; RAG- и security-пробы ловят исключения
    # сами — ни одна не может провалить gather целиком.
    ollama, rag_ready, lt, security = await asyncio.gather(
        llm.healthcheck(),
        _rag_ready_probe(),
        languagetool.healthcheck(),
        _security_probe(),
    )

    return {
        "ok": ollama["ok"],
        "ollama": ollama,
        "rag_ready": rag_ready,
        "languagetool_ready": lt["ok"],
        "security": security,
    }
