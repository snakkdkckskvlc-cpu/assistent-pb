"""Простая FIFO-очередь задач с одним воркером.

На CPU LLM грузит все ядра, поэтому нет смысла в параллелизме — только очередь.
Клиент получает task_id, статус тянется через /api/tasks/{id}.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)


@dataclass
class Task:
    id: str
    kind: str
    # Логин того, кто поставил задачу. Пустая строка бывает только в тестах и
    # в однопользовательском десктопном режиме. По этому полю фильтруется
    # выдача: результат задачи — это разбор договора целиком, и отдавать его
    # по одному лишь знанию id нельзя.
    owner: str = ""
    status: str = "queued"  # queued | running | done | error
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    finished_at: str | None = None
    progress: str = ""
    result: Any = None
    error: str | None = None
    # Число полученных потоковых чанков от Ollama (≈ токенов), кумулятивно
    # на всю задачу — не показывается в живом UI (см. percent ниже), но
    # пишется в историю задач (services/history.py) как диагностика.
    tokens: int = 0
    # Грубая, но честная оценка прогресса 0..100 для полосы загрузки в UI
    # (см. pipelines/_prompts.py::make_progress_counter). Не обязана дойти
    # до 100 сама — финальные 100% показывает фронтенд по status == "done".
    percent: int = 0


class TaskQueue:
    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        # Очередь создаётся в start(), а не здесь — иначе привяжется к
        # event loop, действующему в момент импорта модуля, и упадёт
        # при переиспользовании (например, в тестах с новым event loop).
        self._queue: asyncio.Queue[tuple[Task, Callable[[Task], Awaitable[Any]]]] | None = None
        self._worker_task: asyncio.Task | None = None
        # Колбэк «задача завершена» (успех или ошибка). Назначается снаружи
        # (lifespan main.py пишет историю задач) — сама очередь не знает о
        # сервисах, слои не переворачиваются. Ошибка колбэка не валит воркер.
        self.on_task_finished: Callable[[Task], Awaitable[None]] | None = None

    def start(self) -> None:
        if self._worker_task is None:
            self._queue = asyncio.Queue()
            self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task
            self._worker_task = None

    async def submit(
        self, kind: str, coro_factory: Callable[[Task], Awaitable[Any]], owner: str = ""
    ) -> Task:
        if self._queue is None:
            raise RuntimeError("TaskQueue не запущена — вызовите start() в lifespan")
        task = Task(id=uuid.uuid4().hex[:12], kind=kind, owner=owner)
        self._tasks[task.id] = task
        await self._queue.put((task, coro_factory))
        log.info("Task queued: %s [%s] от %s", task.id, kind, owner or "—")
        return task

    def get(self, task_id: str, owner: str | None = None) -> Task | None:
        """Задача или None. С owner отдаёт только СВОЮ задачу.

        None вместо отказа — сознательно: вызывающий отвечает 404, а не 403.
        403 подтвердил бы, что задача с таким id существует.
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        if owner is not None and task.owner != owner:
            return None
        return task

    def list(self, owner: str | None = None) -> list[Task]:
        tasks = list(self._tasks.values())
        if owner is None:
            return tasks
        return [t for t in tasks if t.owner == owner]

    async def _worker(self) -> None:
        while True:
            task, coro_factory = await self._queue.get()
            task.status = "running"
            task.started_at = datetime.now(UTC).isoformat()
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
                task.finished_at = datetime.now(UTC).isoformat()
                log.info("Task end: %s → %s", task.id, task.status)
                if self.on_task_finished is not None:
                    try:
                        await self.on_task_finished(task)
                    except Exception:
                        log.exception("on_task_finished failed for %s", task.id)


queue = TaskQueue()
