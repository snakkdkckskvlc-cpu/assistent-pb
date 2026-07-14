"""Простая FIFO-очередь задач с одним воркером.

На CPU LLM грузит все ядра, поэтому нет смысла в параллелизме — только очередь.
Клиент получает task_id, статус тянется через /api/tasks/{id}.
"""
from __future__ import annotations

import asyncio
import logging
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)


@dataclass
class Task:
    id: str
    kind: str
    status: str = "queued"  # queued | running | done | error
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    progress: str = ""
    result: Any = None
    error: str | None = None


class TaskQueue:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        # Очередь создаётся в start(), а не здесь — иначе привяжется к
        # event loop, действующему в момент импорта модуля, и упадёт
        # при переиспользовании (например, в тестах с новым event loop).
        self._queue: asyncio.Queue[tuple[Task, Callable[[Task], Awaitable[Any]]]] | None = None
        self._worker_task: asyncio.Task | None = None

    def start(self) -> None:
        if self._worker_task is None:
            self._queue = asyncio.Queue()
            self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

    async def submit(self, kind: str, coro_factory: Callable[[Task], Awaitable[Any]]) -> Task:
        if self._queue is None:
            raise RuntimeError("TaskQueue не запущена — вызовите start() в lifespan")
        task = Task(id=uuid.uuid4().hex[:12], kind=kind)
        self._tasks[task.id] = task
        await self._queue.put((task, coro_factory))
        log.info("Task queued: %s [%s]", task.id, kind)
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        return list(self._tasks.values())

    async def _worker(self) -> None:
        while True:
            task, coro_factory = await self._queue.get()
            task.status = "running"
            task.started_at = datetime.now(timezone.utc).isoformat()
            log.info("Task start: %s [%s]", task.id, task.kind)
            try:
                task.result = await coro_factory(task)
                task.status = "done"
            except Exception as e:
                log.exception("Task failed: %s", task.id)
                task.status = "error"
                task.error = f"{type(e).__name__}: {e}"
                task.result = {"traceback": traceback.format_exc()}
            finally:
                task.finished_at = datetime.now(timezone.utc).isoformat()
                log.info("Task end: %s → %s", task.id, task.status)


queue = TaskQueue()
