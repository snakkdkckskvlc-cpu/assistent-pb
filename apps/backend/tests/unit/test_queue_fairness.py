"""Круговой обход очереди по владельцам.

Сотрудников тридцать, а считающая задача одна. При честном FIFO человек,
отправивший десять документов подряд, занимает сервер на весь день, и все
остальные ждут за ним — даже те, кто пришёл с одним письмом.
"""

from __future__ import annotations

import asyncio

import pytest
from fire_safety_backend.infrastructure.queue import TaskQueue


async def _noop(task):
    return {"ok": True}


@pytest.fixture
def q() -> TaskQueue:
    queue = TaskQueue()
    queue._queue = asyncio.Queue()  # без запуска воркера: проверяем планирование
    return queue


def _submit(q: TaskQueue, owner: str, n: int = 1) -> list:
    async def go():
        return [await q.submit("legal", _noop, owner=owner) for _ in range(n)]

    return asyncio.run(go())


def test_one_person_with_a_batch_does_not_block_everyone(q: TaskQueue) -> None:
    """Ключевой случай: Иванов отправил пачку, следом пришли двое с одним
    документом. Они не должны ждать всю пачку."""
    _submit(q, "ivanov", 5)
    _submit(q, "petrova")
    _submit(q, "sidorov")

    owners = [t.owner for t in q.planned_order()]
    # После первой задачи Иванова обслуживаются пришедшие позже — и только
    # потом остаток его пачки.
    assert owners[:3] == ["ivanov", "petrova", "sidorov"], owners
    assert owners.count("ivanov") == 5


def test_order_within_one_owner_stays_fifo(q: TaskQueue) -> None:
    tasks = _submit(q, "ivanov", 3)
    order = [t.id for t in q.planned_order()]
    assert order == [t.id for t in tasks]


def test_single_task_goes_first(q: TaskQueue) -> None:
    task = _submit(q, "ivanov")[0]
    assert q.planned_order() == [task]
    assert q.position(task.id) == 1


def test_position_matches_the_real_order(q: TaskQueue) -> None:
    """Позиция обязана считаться по фактическому порядку, а не по времени
    поступления: при круговом обходе задача, отправленная позже, часто
    считается раньше."""
    _submit(q, "ivanov", 3)
    late = _submit(q, "petrova")[0]
    assert q.position(late.id) == 2, "пришёл последним, но считается вторым"


def test_tasks_without_owner_do_not_break_planning(q: TaskQueue) -> None:
    _submit(q, "", 2)
    _submit(q, "ivanov")
    assert len(q.planned_order()) == 3
