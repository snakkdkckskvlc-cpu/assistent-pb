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

# Сколько задач держать в памяти. Сервер живёт неделями, и словарь рос без
# предела. Вытеснять безопасно: результаты завершённых задач сохранены в базе
# (infrastructure/task_store.py), и /api/tasks/{id} подхватит их оттуда.
_MAX_TASKS_IN_MEMORY = 200


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
        # Ожидающие заявки в порядке поступления. Отдельно от _queue: очередь
        # asyncio отдаёт строго первого в списке, а нам нужно ВЫБИРАТЬ —
        # см. _next_pending.
        self._pending: list[tuple[Task, Callable[[Task], Awaitable[Any]]]] = []
        # Кто считался последним. По этому списку идёт круговой обход
        # владельцев: недавно обслуженный уходит в конец.
        self._recent_owners: list[str] = []
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
        self._evict_finished()
        self._pending.append((task, coro_factory))
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

    def _evict_finished(self) -> None:
        """Выкидывает самые старые ЗАВЕРШЁННЫЕ задачи сверх предела.

        Только завершённые: выкинуть ждущую или считающуюся значило бы потерять
        её у пользователя прямо во время работы.
        """
        if len(self._tasks) <= _MAX_TASKS_IN_MEMORY:
            return
        finished = sorted(
            (t for t in self._tasks.values() if t.status in ("done", "error")),
            key=lambda t: t.created_at,
        )
        for task in finished[: len(self._tasks) - _MAX_TASKS_IN_MEMORY]:
            self._tasks.pop(task.id, None)

    def running(self) -> Task | None:
        """Задача, которую воркер считает прямо сейчас. Она одна: параллелить
        на CPU смысла нет, и замер это подтвердил — Ollama всё равно выполняет
        запросы к одной модели последовательно."""
        return next((t for t in self._tasks.values() if t.status == "running"), None)

    def planned_order(self) -> list[Task]:
        """Ожидающие задачи в том порядке, в котором они РЕАЛЬНО пойдут.

        Считается тем же круговым обходом, что и в _next_pending: показывать
        позицию по времени поступления было бы враньём — при круговом обходе
        задача, отправленная позже, часто считается раньше.
        """
        pending = list(self._pending)
        recent = list(self._recent_owners)
        order: list[Task] = []
        while pending:

            def staleness(item: tuple[Task, Any], recent: list[str] = recent) -> tuple[int, str]:
                owner = item[0].owner
                idx = recent.index(owner) if owner in recent else -1
                return (idx, item[0].created_at)

            chosen = min(pending, key=staleness)
            pending.remove(chosen)
            owner = chosen[0].owner
            if owner in recent:
                recent.remove(owner)
            recent.append(owner)
            order.append(chosen[0])
        return order

    def queued_ahead(self, task_id: str) -> list[Task]:
        """Задачи, которые будут посчитаны раньше указанной. Пустой список —
        задача следующая, уже считается или завершена."""
        task = self._tasks.get(task_id)
        if task is None or task.status != "queued":
            return []
        order = self.planned_order()
        if task not in order:
            return []
        return order[: order.index(task)]

    def position(self, task_id: str) -> int:
        """Место в очереди: 1 — следующая на выполнение. 0 — позиции нет.

        Показывать позицию важнее, чем кажется: задача на CPU идёт минутами, и
        без неё интерфейс просто молчит, а человек не понимает, работает
        программа или зависла.

        НОЛЬ, а не единица, когда задача числится ожидающей, но в плане её нет.
        Раньше такая задача получала от queued_ahead пустой список и становилась
        «первой в очереди» — навсегда. Человек видел «Следующая в очереди» и
        ждал ответа, которого не будет: это то же самое молчаливое враньё, что
        и «задача в очереди» у прерванной перезапуском. Лучше не показать
        позицию, чем показать неверную.
        """
        task = self._tasks.get(task_id)
        if task is None or task.status != "queued":
            return 0
        if not any(t is task for t in self.planned_order()):
            log.warning("Задача %s числится в очереди, но её нет в плане", task_id)
            return 0
        return len(self.queued_ahead(task_id)) + 1

    def queued_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == "queued")

    def list(self, owner: str | None = None) -> list[Task]:
        tasks = list(self._tasks.values())
        if owner is None:
            return tasks
        return [t for t in tasks if t.owner == owner]

    def _next_pending(self) -> tuple[Task, Callable[[Task], Awaitable[Any]]]:
        """Следующая задача: круговой обход по владельцам, а не строгая очередь.

        Зачем. Сотрудников тридцать, а считающая задача одна. При честном FIFO
        человек, отправивший десять документов подряд, занимает сервер на весь
        день, и все остальные ждут за ним — даже те, кто пришёл с одним
        письмом. Отказ выглядит как «программа не работает», хотя она работает
        и занята чужой пачкой.

        Правило: из ожидающих берётся самая ранняя заявка того владельца,
        которого обслуживали дольше всех. Внутри одного владельца порядок
        остаётся честным FIFO, между владельцами — по кругу. Один человек с
        десятью документами больше не отодвигает девятерых с одним.
        """
        # Короткого замыкания на «одну задачу в очереди» здесь БЫТЬ НЕ ДОЛЖНО.
        # Оно возвращало задачу, не отметив её владельца обслуженным, — а на
        # этом железе это основной путь: задача идёт минутами, и воркер почти
        # всегда просыпается ровно на одной заявке. Круговой обход в итоге не
        # работал вовсе: Иванов отправлял документ, потом второй, следом
        # Петрова — и Иванов обслуживался дважды подряд, ровно то, ради чего
        # правка делалась. Общий путь на одной задаче стоит столько же.

        # Владелец, который дольше всех не получал очереди, — первый.
        def staleness(item: tuple[Task, Any]) -> tuple[int, str]:
            owner = item[0].owner
            recent = self._recent_owners.index(owner) if owner in self._recent_owners else -1
            return (recent, item[0].created_at)

        chosen = min(self._pending, key=staleness)
        self._pending.remove(chosen)
        owner = chosen[0].owner
        if owner in self._recent_owners:
            self._recent_owners.remove(owner)
        self._recent_owners.append(owner)
        return chosen

    async def _worker(self) -> None:
        while True:
            await self._queue.get()
            # Что именно считать, решает _next_pending, а не порядок в
            # asyncio.Queue: она здесь только будильник «работа появилась».
            task, coro_factory = self._next_pending()
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
