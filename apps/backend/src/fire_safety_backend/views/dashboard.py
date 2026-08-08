"""Роутер: сводка для стартового экрана «Сегодня».

Зачем экран вообще. Задача на CPU идёт минутами, а результат жил только в
открытой вкладке: ушёл со страницы — потерял. История хранила лишь сводку и
открыть прошлый разбор не давала. «Сегодня» превращает ожидание из тупика в
фон — запустил проверку, ушёл писать письмо, вернулся и забрал.

Одна ручка, а не три: экран открывается первым и опрашивается раз в несколько
секунд, и три запроса на каждый показ дали бы втрое больше обращений к SQLite
на общем сервере.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from ..infrastructure.queue import queue
from ..services import history, transport
from . import auth

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

_ACTIVE = ("queued", "running")


def _today_iso() -> str:
    return datetime.now(UTC).date().isoformat()


def _collect(login: str) -> dict:
    tasks = queue.list(owner=login)
    active = [
        {
            "id": t.id,
            "kind": t.kind,
            "status": t.status,
            "progress": t.progress,
            "percent": t.percent,
            "position": queue.position(t.id),
        }
        for t in sorted(tasks, key=lambda x: x.created_at)
        if t.status in _ACTIVE
    ]

    # Готовое берём из истории, а не из очереди: очередь держит в памяти
    # последние двести задач и вытесняет старые, а история — постоянная.
    recent = [r for r in history.list_recent(limit=30, owner=login) if r["status"] == "done"][:6]

    today = _today_iso()
    done_today = sum(
        1
        for r in history.list_recent(limit=200, owner=login)
        if r["status"] == "done" and str(r["finished_at"] or "").startswith(today)
    )

    # Рейсы — общие для компании, а не «мои»: машину выдаёт один человек,
    # а спрашивают о ней все.
    on_the_road = [t for t in transport.list_trips(limit=100) if t.returned_at is None]

    # Занята ли модель — состояние ОБЩЕЕ, не «моё». Считающая задача одна на
    # всю компанию, и человек, запустивший разбор, будет ждать чужой, даже не
    # понимая почему. «Программа зависла» — именно отсюда.
    #
    # Чья это задача и над каким документом — не отдаём: разбор договора видеть
    # должен только его владелец, а для объяснения ожидания хватает «занято» и
    # числа ждущих.
    running = queue.running()

    return {
        "date": today,
        "llm": {
            "busy": running is not None,
            "waiting": queue.queued_count(),
        },
        "metrics": {
            "active": len(active),
            "on_the_road": len(on_the_road),
            "done_today": done_today,
        },
        "active": active,
        "recent": recent,
        "trips": [
            {
                "id": t.id,
                "vehicle": t.vehicle_name,
                "driver": t.driver,
                "departed_at": t.departed_at,
                "to": t.place_to_name or t.destination_text,
            }
            for t in on_the_road[:6]
        ],
    }


@router.get("")
async def api_dashboard(user: auth.User = Depends(auth.current_user)) -> dict:
    # Сводка лезет в SQLite трижды — уводим с event loop, иначе на общем
    # сервере опрос этого экрана начнёт подтормаживать чужие запросы.
    return await asyncio.to_thread(_collect, user.login)
