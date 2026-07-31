"""Роутер проверки готовности: Ollama + RAG."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter

from .. import config
from ..infrastructure import bitlocker, integrity, languagetool, llm, netguard, secure_files

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])


async def _rag_probe() -> tuple[bool, str]:
    """Готовность нормативной базы и — если не готова — ПОЧЕМУ.

    Причина важна: «не подключена» из-за пустого индекса и из-за нескачанной
    модели эмбеддингов выглядят одинаково, а лечатся совершенно по-разному.
    Приложению запрещён выход в интернет, поэтому модель обязана быть скачана
    заранее, и молчать об её отсутствии нельзя.
    """
    try:
        import fire_safety_rag

        # Использует закешированный singleton-ретривер (lru_cache) —
        # без этого каждый health-чек заново грузил бы embedding-модель.
        if await asyncio.to_thread(fire_safety_rag.is_ready):
            return True, ""
        if not await asyncio.to_thread(fire_safety_rag.embed_model_cached):
            return False, (
                "Модель эмбеддингов не скачана. Запустите "
                "scripts/warm_models.py на машине с доступом в интернет."
            )
        return False, "Индекс нормативной базы пуст или не создан."
    except Exception as e:
        log.warning("RAG probe failed: %s", e)
        return False, "Нормативная база недоступна."


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
    net = netguard.status()
    # Сверка 87 файлов — 27 мс, но health опрашивается интерфейсом постоянно,
    # поэтому всё равно в поток, рядом с BitLocker.
    try:
        code = await asyncio.to_thread(integrity.status)
    except Exception as e:  # pragma: no cover — verify() исключений не бросает
        log.warning("Integrity probe failed: %s", e)
        code = "unknown"
    return {
        "encryption": st.mode,
        "encryption_reason": st.reason,
        "encryption_broken": st.broken,
        "retention_days": config.DATA_RETENTION_DAYS,
        "bitlocker": drive_encryption,
        "network": net["mode"],
        "network_reason": net["reason"],
        # Пустой список — тоже результат: он означает, что за сессию
        # приложение наружу не пыталось.
        "network_blocked_attempts": net["blocked_attempts"],
        "network_blocked_targets": net["blocked_targets"],
        "code_integrity": code,
    }


@router.get("/health")
async def health() -> dict:
    # Независимые пробы — параллельно, а не суммируя их латентности.
    # ollama/languagetool.healthcheck() уже сами перехватывают httpx.HTTPError
    # и возвращают {"ok": False, ...}; RAG- и security-пробы ловят исключения
    # сами — ни одна не может провалить gather целиком.
    ollama, rag, lt, security = await asyncio.gather(
        llm.healthcheck(),
        _rag_probe(),
        languagetool.healthcheck(),
        _security_probe(),
    )
    rag_ready, rag_warning = rag

    return {
        "ok": ollama["ok"],
        "ollama": ollama,
        "rag_ready": rag_ready,
        "rag_warning": rag_warning,
        "languagetool_ready": lt["ok"],
        "security": security,
    }
