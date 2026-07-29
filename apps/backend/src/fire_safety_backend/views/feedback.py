"""Роутер фидбека по результатам (👍/👎)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter

from ..infrastructure.queue import queue
from ..models import FeedbackCreate
from ..services import feedback as service

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", status_code=201)
async def create_feedback(payload: FeedbackCreate) -> dict:
    # Ответ модели берём из живой задачи в очереди: в историю задач
    # (services/history.py) он намеренно не пишется — там только короткая
    # сводка, потому что результат бывает большим и содержит текст документов.
    # Момент нажатия 👎 — единственный, когда полный результат ещё под рукой:
    # после перезапуска приложения очередь пуста.
    task = queue.get(payload.task_id)
    await asyncio.to_thread(service.create, payload, task.result if task else None)
    return {"ok": True}
