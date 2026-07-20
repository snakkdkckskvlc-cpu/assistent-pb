"""Роутер: статус и список фоновых задач."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..infrastructure.queue import queue

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("/{task_id}")
async def api_task(task_id: str) -> dict:
    task = queue.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
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
    }


@router.get("")
async def api_tasks_list() -> list[dict]:
    tasks = queue.list()
    return [
        {"id": t.id, "kind": t.kind, "status": t.status, "created_at": t.created_at}
        for t in sorted(tasks, key=lambda x: x.created_at, reverse=True)[:50]
    ]
