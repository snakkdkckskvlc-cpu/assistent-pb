"""Позиция в очереди и оценка времени ожидания.

Зачем это вообще. Параллелить смысла нет — замерено, что Ollama выполняет
запросы к одной модели строго последовательно (2 и 3 одновременных дали
24.7/48.8 и 24.6/49.1/73.2 с, скорость каждого не изменилась). Значит на
сервере с несколькими сотрудниками ожидание неизбежно, и единственное, что
можно дать человеку, — честный ответ «сколько ещё».

Без этого интерфейс при ожидании молчит, и «работает или зависло» неотличимо.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fire_safety_backend.infrastructure.queue import Task, TaskQueue


def _task(task_id: str, kind: str = "legal", status: str = "queued", age_sec: int = 0) -> Task:
    created = (datetime.now(UTC) - timedelta(seconds=age_sec)).isoformat()
    return Task(id=task_id, kind=kind, status=status, created_at=created)


@pytest.fixture
def q() -> TaskQueue:
    queue = TaskQueue()
    return queue


async def _noop(task: Task) -> None:  # pragma: no cover — до выполнения не доходит
    return None


def _fill(queue: TaskQueue, *tasks: Task) -> None:
    """Кладёт задачи в очередь так же, как это делает submit().

    Раньше здесь была запись только в `_tasks`, минуя список ожидающих. Из-за
    этого тесты перестали проверять настоящий путь: `planned_order()` строится
    по `_pending`, видел пустоту — и позиция у всех выходила первой. Три теста
    падали, и это была не ложная тревога, а честный сигнал, что фикстура
    разошлась с реализацией.

    Настоящий `submit()` здесь не годится: он требует запущенного цикла и
    поднимает воркер, который тут же начнёт задачи выполнять.
    """
    for t in tasks:
        queue._tasks[t.id] = t
        if t.status == "queued":
            queue._pending.append((t, _noop))


# --- Позиция ---


def test_first_in_line_is_position_one(q: TaskQueue) -> None:
    first = _task("a", age_sec=30)
    _fill(q, first)
    assert q.position("a") == 1


def test_order_is_by_arrival(q: TaskQueue) -> None:
    _fill(q, _task("a", age_sec=30), _task("b", age_sec=20), _task("c", age_sec=10))
    assert q.position("a") == 1
    assert q.position("b") == 2
    assert q.position("c") == 3


def test_running_task_has_no_position(q: TaskQueue) -> None:
    """Она уже не ждёт — показывать ей «вы 1-й в очереди» неправильно."""
    _fill(q, _task("running", status="running"))
    assert q.position("running") == 0


def test_finished_task_has_no_position(q: TaskQueue) -> None:
    _fill(q, _task("done", status="done"))
    assert q.position("done") == 0


def test_unknown_task_has_no_position(q: TaskQueue) -> None:
    assert q.position("нет-такой") == 0


def test_finished_tasks_do_not_inflate_the_queue(q: TaskQueue) -> None:
    """Завершённые остаются в памяти, но местом в очереди не считаются."""
    _fill(q, _task("old", status="done", age_sec=99), _task("mine", age_sec=10))
    assert q.position("mine") == 1
    assert q.queued_count() == 1


def test_running_task_is_found(q: TaskQueue) -> None:
    _fill(q, _task("q1"), _task("r1", status="running"))
    running = q.running()
    assert running is not None
    assert running.id == "r1"


def test_queued_ahead_lists_only_earlier_tasks(q: TaskQueue) -> None:
    _fill(q, _task("a", age_sec=30), _task("b", age_sec=20), _task("c", age_sec=10))
    assert [t.id for t in q.queued_ahead("c")] == ["a", "b"]
    assert q.queued_ahead("a") == []


# --- Оценка времени ---


def test_eta_uses_history(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Число берётся из фактических замеров на ЭТОЙ машине, а не из константы:
    разброс между «письмо» и «договор на 40 страниц» огромный, и от железа он
    тоже зависит."""
    from fire_safety_backend.infrastructure.queue import queue as real_queue
    from fire_safety_backend.views import tasks as tasks_view

    monkeypatch.setattr(tasks_view.history, "typical_duration", lambda kind, limit=10: 120.0)
    ahead = _task("ahead", age_sec=30)
    mine = _task("mine", age_sec=10)
    # Через _fill, а не прямой записью в _tasks: позиция считается по списку
    # ожидающих, и задача, которой там нет, позиции не имеет вовсе.
    _fill(real_queue, ahead, mine)
    try:
        # Впереди одна задача плюс своя — два раза по 120 секунд.
        assert tasks_view._eta_seconds(mine) == pytest.approx(240.0)
    finally:
        real_queue._tasks.pop("ahead", None)
        real_queue._tasks.pop("mine", None)
        real_queue._pending.clear()


def test_eta_is_none_without_history(client, monkeypatch: pytest.MonkeyPatch) -> None:
    """Статистики нет — молчим, а не выдумываем число."""
    from fire_safety_backend.infrastructure.queue import queue as real_queue
    from fire_safety_backend.views import tasks as tasks_view

    monkeypatch.setattr(tasks_view.history, "typical_duration", lambda kind, limit=10: None)
    mine = _task("mine2")
    real_queue._tasks["mine2"] = mine
    try:
        assert tasks_view._eta_seconds(mine) is None
    finally:
        real_queue._tasks.pop("mine2", None)


def test_typical_duration_is_median_not_mean(client) -> None:
    """Один договор на 40 страниц не должен сдвигать оценку для всех
    последующих — поэтому медиана."""
    from fire_safety_backend.infrastructure.queue import Task as RealTask
    from fire_safety_backend.services import history

    base = datetime.now(UTC)
    for i, seconds in enumerate([10, 12, 11, 3600]):
        started = base - timedelta(seconds=seconds)
        history.record(
            RealTask(
                id=f"h{i}",
                kind="legal",
                status="done",
                created_at=started.isoformat(),
                started_at=started.isoformat(),
                finished_at=base.isoformat(),
            )
        )
    assert history.typical_duration("legal") < 60


def test_typical_duration_ignores_failed_tasks(client) -> None:
    """Упавшая через секунду задача — не показатель того, сколько ждать."""
    from fire_safety_backend.infrastructure.queue import Task as RealTask
    from fire_safety_backend.services import history

    base = datetime.now(UTC)
    history.record(
        RealTask(
            id="err1",
            kind="letter",
            status="error",
            created_at=base.isoformat(),
            started_at=base.isoformat(),
            finished_at=base.isoformat(),
        )
    )
    assert history.typical_duration("letter") is None
