"""Роутер: статус и список фоновых задач."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException

from ..infrastructure.queue import Task, queue
from ..services import history
from . import auth

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _elapsed(task: Task) -> float:
    if not task.started_at:
        return 0.0
    return (datetime.now(UTC) - datetime.fromisoformat(task.started_at)).total_seconds()


def _eta_seconds(task: Task) -> float | None:
    """Сколько ещё ждать. None — статистики нет, и врать числом не надо.

    Складываем ОСТАТОК считающейся сейчас задачи и типичную длительность
    каждой, кто стоит впереди. Типичная берётся из истории по этой самой
    машине (services/history.py::typical_duration) — константу в код зашивать
    нельзя, разброс между «письмо» и «договор на 40 страниц» огромный, и от
    железа он тоже зависит.
    """
    known: list[float] = []

    running = queue.running()
    if running is not None:
        typical = history.typical_duration(running.kind)
        if typical is not None:
            known.append(max(typical - _elapsed(running), 0.0))

    for ahead in [*queue.queued_ahead(task.id), task]:
        typical = history.typical_duration(ahead.kind)
        if typical is not None:
            known.append(typical)

    return sum(known) if known else None


@router.get("/{task_id}")
async def api_task(task_id: str, user: auth.User = Depends(auth.current_user)) -> dict:
    # Чужая задача отдаёт 404, а не 403. Результат здесь — разбор договора
    # ЦЕЛИКОМ, вместе с текстом документа, и 403 подтвердил бы посторонему,
    # что задача с таким id существует.
    task = queue.get(task_id, owner=user.login)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Оценка лезет в SQLite за историей — уводим с event loop.
    eta = await asyncio.to_thread(_eta_seconds, task) if task.status == "queued" else None

    return {
        "id": task.id,
        "kind": task.kind,
        "status": task.status,
        "progress": task.progress,
        "percent": task.percent,
        "result": task.result,
        "error": task.error,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        # Позиция в очереди: 1 — следующая. Без неё интерфейс при ожидании
        # молчит, и человек не понимает, работает программа или зависла.
        "position": queue.position(task.id),
        "queue_length": queue.queued_count(),
        "eta_sec": eta,
    }


@router.get("")
async def api_tasks_list(user: auth.User = Depends(auth.current_user)) -> list[dict]:
    tasks = queue.list(owner=user.login)
    return [
        {"id": t.id, "kind": t.kind, "status": t.status, "created_at": t.created_at}
        for t in sorted(tasks, key=lambda x: x.created_at, reverse=True)[:50]
    ]
