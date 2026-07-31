"""Роутер: состояние защиты данных и ручная очистка рабочих файлов."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from .. import config
from ..infrastructure import secure_files
from ..services import retention

router = APIRouter(prefix="/api/data", tags=["data"])


@router.get("/status")
async def api_data_status() -> dict:
    st = secure_files.status()
    return {
        "encryption": st.mode,
        "encryption_reason": st.reason,
        "encryption_broken": st.broken,
        "retention_days": config.DATA_RETENTION_DAYS,
    }


@router.post("/purge")
async def api_data_purge() -> dict:
    """Удаляет загруженные и сгенерированные файлы, не дожидаясь срока.

    Скачанные документы у пользователя не затрагиваются — удаляются только
    рабочие копии внутри data/.
    """
    # Обход каталогов и удаление — блокирующий I/O.
    return await asyncio.to_thread(retention.purge_all)
