"""Роутер фидбека по результатам (👍/👎)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends

from ..infrastructure import task_store
from ..infrastructure.queue import queue
from ..models import FeedbackCreate
from ..services import feedback as service
from . import auth

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", status_code=201)
async def create_feedback(
    payload: FeedbackCreate, user: auth.User = Depends(auth.current_user)
) -> dict:
    # Ответ модели берём из живой задачи в очереди: в историю задач
    # (services/history.py) он намеренно не пишется — там только короткая
    # сводка, потому что результат бывает большим и содержит текст документов.
    # Момент нажатия 👎 — единственный, когда полный результат ещё под рукой:
    # после перезапуска приложения очередь пуста.
    #
    # owner обязателен и здесь: иначе чужой task_id вытащил бы полный ответ
    # модели по чужому договору в таблицу feedback.bad_output.
    task = queue.get(payload.task_id, owner=user.login)
    if task is None:
        # После перезапуска очередь пуста, но результат сохранён — теперь 👎
        # доносит ответ модели и в этом случае.
        task = await asyncio.to_thread(task_store.load, payload.task_id, user.login)
    await asyncio.to_thread(service.create, payload, task.result if task else None)
    return {"ok": True}
