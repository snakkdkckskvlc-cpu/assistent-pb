"""Роутер истории задач."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ..services import history as service
from . import auth

router = APIRouter(prefix="/api/history", tags=["history"])


@router.get("")
async def api_history(limit: int = 50, user: auth.User = Depends(auth.current_user)) -> list[dict]:
    return await asyncio.to_thread(service.list_recent, min(max(limit, 1), 200), user.login)


@router.delete("", status_code=204, response_model=None)
async def api_history_clear(user: auth.User = Depends(auth.current_user)) -> None:
    # Только свою: на общем сервере кнопка «очистить историю» не должна
    # стирать работу коллег.
    await asyncio.to_thread(service.clear, user.login)
