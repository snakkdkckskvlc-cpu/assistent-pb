"""Роутер фидбека по результатам (👍/👎)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..models import FeedbackCreate
from ..services import feedback as service

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", status_code=201)
async def create_feedback(payload: FeedbackCreate) -> dict:
    await asyncio.to_thread(service.create, payload)
    return {"ok": True}
