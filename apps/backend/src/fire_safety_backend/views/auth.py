"""Роутер входа и зависимость «текущий пользователь».

Зависимость навешивается на роутеры целиком в main.py::create_app, а не на
каждую ручку по отдельности: список ручек растёт, и забыть `Depends` на новой —
вопрос времени. Забытая защита здесь означает открытый доступ к договорам
компании из всей сети.
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote, unquote

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from ..services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "assistent_pb_session"

# Логин, которым в прошлый раз входили с ЭТОГО устройства. Отдельная cookie и
# намеренно БЕЗ httponly: её читает страница входа, чтобы подставить логин в
# поле — сотруднику остаётся нажать кнопку. Секрета в ней нет: сам по себе
# логин доступа не даёт, доступ даёт сессионная cookie выше.
LAST_LOGIN_COOKIE = "assistent_pb_login"
_LAST_LOGIN_MAX_AGE = 365 * 24 * 3600

# Псевдоним, чтобы остальные роутеры не тянули слой сервисов ради аннотации.
User = auth_service.User


def _remember_login(response: Response, login: str) -> None:
    # Значение cookie кодируется процентами. Заголовки HTTP допускают только
    # latin-1, и кириллический логин ронял вход целиком: «Разраб» приводил к
    # UnicodeEncodeError внутри starlette и HTTP 500 вместо входа. Для русской
    # компании это отказ на самом обычном сценарии — логины здесь кириллицей и
    # будут. Обратное преобразование — в /api/auth/state, который и отдаёт
    # запомненный логин странице входа.
    response.set_cookie(
        LAST_LOGIN_COOKIE,
        quote(login, safe=""),
        httponly=False,  # её читает страница входа — в этом весь смысл
        samesite="lax",
        path="/",
        max_age=_LAST_LOGIN_MAX_AGE,
    )


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
    """Только логин. Пароля нет — см. services/auth.py, там же честная
    оговорка о том, чего это стоит."""

    login: str = Field(min_length=1, max_length=100)


@router.post("/login")
async def api_login(payload: LoginRequest, response: Response) -> dict:
    user = await asyncio.to_thread(auth_service.authenticate, payload.login)
    if user is None:
        # «Нет такого логина» и «запись отключена» отвечают одинаково: даже без
        # пароля не стоит превращать форму входа в справочник существующих
        # логинов.
        raise HTTPException(status_code=401, detail="Такой учётной записи нет")
    token = await asyncio.to_thread(auth_service.open_session, user.id)
    _set_cookie(response, token)
    # Запоминаем логин на этом устройстве: в следующий раз он подставится сам,
    # и сотруднику останется нажать «Вход».
    _remember_login(response, user.login)
    return {"login": user.login, "is_admin": user.is_admin}


@router.post("/logout")
async def api_logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        await asyncio.to_thread(auth_service.close_session, token)
    response.delete_cookie(COOKIE_NAME, path="/")
    # Логин НЕ забываем: выход — это «закончил работу», а не «это не мой
    # компьютер». Иначе следующий вход снова требовал бы набирать логин, ради
    # чего всё и делалось. Забыть устройство — отдельная кнопка ниже.
    return {"ok": True}


@router.post("/forget-device")
async def api_forget_device(request: Request, response: Response) -> dict:
    """Перестать подставлять логин на этом компьютере.

    Нужна для общего или чужого компьютера: там подставленный логин коллеги —
    это приглашение войти под ним.
    """
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        await asyncio.to_thread(auth_service.close_session, token)
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(LAST_LOGIN_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def api_me(request: Request) -> dict:
    """Кто вошёл, какой логин помнит это устройство и есть ли вообще записи.

    `remembered` — то, ради чего сделан весь этот вход: страница подставляет
    его в поле, и остаётся нажать кнопку.

    `any_users` нужен на свежем сервере: записей ещё нет, и честное «заведите
    скриптом» лучше, чем «такой учётной записи нет» на любой ввод.
    """
    user = await optional_user(request)
    return {
        "login": user.login if user else None,
        "is_admin": bool(user and user.is_admin),
        # Cookie хранится в процентном кодировании (кириллица в заголовок
        # HTTP иначе не помещается) — раскодируем перед отдачей на страницу.
        "remembered": unquote(request.cookies.get(LAST_LOGIN_COOKIE, "")),
        "any_users": await asyncio.to_thread(auth_service.any_users_exist),
    }
