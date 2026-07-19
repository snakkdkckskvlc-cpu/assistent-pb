"""Роутер истории задач."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..services import history as service

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
async def api_history(limit: int = 50) -> list[dict]:
    return await asyncio.to_thread(service.list_recent, min(max(limit, 1), 200))


@router.delete("", status_code=204, response_model=None)
async def api_history_clear() -> None:
    await asyncio.to_thread(service.clear)
