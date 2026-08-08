"""Роутер: сводка «что происходит» — как приложением пользуются."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query

from ..services import stats as stats_service
from . import auth

router = APIRouter(prefix="/api/stats", tags=["stats"])

# Окна, за которые считаем. Список закрытый: параметр приходит из браузера и
# уходит в SQL модификатором даты, поэтому произвольное число туда не пускаем.
_ALLOWED_DAYS = (7, 30, 90)


@router.get("")
async def api_stats(
    days: int = Query(default=30), user: auth.User = Depends(auth.current_user)
) -> dict:
    """Сводка за период. Разбивка по сотрудникам — только администратору.

    Обычный сотрудник видит, чем пользуется компания и где чаще жмут «палец
    вниз», но не видит, кто сколько задач запустил: это наблюдение за
    коллегами, а не рабочая информация. Права проверяются здесь, а не в
    сервисе, — сервис отдаёт то, что попросили.
    """
    window = days if days in _ALLOWED_DAYS else 30
    data = await asyncio.to_thread(stats_service.collect, window, with_people=bool(user.is_admin))
    data["окна"] = list(_ALLOWED_DAYS)
    return data
