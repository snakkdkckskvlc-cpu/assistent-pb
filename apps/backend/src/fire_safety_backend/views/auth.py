"""Роутер входа и зависимость «текущий пользователь».

Зависимость навешивается на роутеры целиком в main.py::create_app, а не на
каждую ручку по отдельности: список ручек растёт, и забыть `Depends` на новой —
вопрос времени. Забытая защита здесь означает открытый доступ к договорам
компании из всей сети.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "assistent_pb_session"

# Псевдоним, чтобы остальные роутеры не тянули слой сервисов ради аннотации.
User = auth_service.User


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,  # недоступна из JS: XSS не утащит сессию
        samesite="lax",  # чужая страница не отправит запрос от имени пользователя
        path="/",
        max_age=auth_service.SESSION_IDLE_HOURS * 3600,
        # secure=True НЕ ставим осознанно: внутри сети работает обычный HTTP, и
        # с этим флагом cookie просто не отправлялась бы. Записано в
        # docs/07-ops/install-server.md, а не умолчано.
    )


async def current_user(request: Request) -> auth_service.User:
    """Пользователь или 401. Сессия проверяется в БД на каждый запрос —
    значит выход и отключение учётной записи действуют немедленно."""
    token = request.cookies.get(COOKIE_NAME, "")
    user = await asyncio.to_thread(auth_service.user_for_session, token)
    if user is None:
        raise HTTPException(status_code=401, detail="Требуется вход")
    return user


async def optional_user(request: Request) -> auth_service.User | None:
    """Для HTML-страниц: без входа их не показываем, но и 401 в браузере
    выглядит как поломка — страницы сами уводят на форму входа."""
    token = request.cookies.get(COOKIE_NAME, "")
    return await asyncio.to_thread(auth_service.user_for_session, token)


class LoginRequest(BaseModel):
    login: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


@router.post("/login")
async def api_login(payload: LoginRequest, response: Response) -> dict:
    user = await asyncio.to_thread(auth_service.authenticate, payload.login, payload.password)
    if user is None:
        # Одно сообщение на «нет такого логина» и «неверный пароль»: раздельные
        # подсказали бы перебирающему, какие логины существуют.
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    token = await asyncio.to_thread(auth_service.open_session, user.id)
    _set_cookie(response, token)
    return {"login": user.login, "is_admin": user.is_admin}


@router.post("/logout")
async def api_logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        await asyncio.to_thread(auth_service.close_session, token)
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
async def api_me(request: Request) -> dict:
    """Кто вошёл и заведены ли вообще учётные записи.

    Второе нужно странице входа: на свежем сервере пользователей ещё нет, и
    честное «учётных записей нет, заведите скриптом» лучше, чем бесконечное
    «неверный логин или пароль» на любой ввод.
    """
    user = await optional_user(request)
    return {
        "login": user.login if user else None,
        "is_admin": bool(user and user.is_admin),
        "any_users": await asyncio.to_thread(auth_service.any_users_exist),
    }
