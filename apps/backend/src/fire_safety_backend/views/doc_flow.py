"""Роутер: журнал прохождения документов.

Синхронно и без очереди: модели здесь нет, всё — запросы к SQLite.

Видно всем вошедшим, а не только владельцу записи. Это отличается от правила
для файлов в data/outputs и сделано осознанно: смысл журнала в том, чтобы
снабженец увидел, что его счёт у финдиректора. При разграничении по владельцу
он бы этого не увидел, и функция потеряла бы смысл.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..services import doc_flow as service
from . import auth

router = APIRouter(prefix="/api/doc-flow", tags=["doc-flow"])


class НовыйДокумент(BaseModel):
    kind: str
    number: str = ""
    counterparty: str = ""
    subject: str = ""
    # Копейками, целым числом: пересчёт из рублей делает интерфейс, как везде
    # в проекте.
    amount_kop: int | None = None
    due_at: str | None = None
    # Пусто — документ остаётся у того, кто его завёл.
    holder: str = ""


class Передача(BaseModel):
    to: str
    note: str = ""


class СменаСостояния(BaseModel):
    to: str
    note: str = ""


class Справочник(BaseModel):
    виды: list[dict]
    состояния: list[str]
    переходы: dict[str, list[str]]


@router.get("/reference")
async def api_reference(user: auth.User = Depends(auth.current_user)) -> Справочник:
    """Виды документов и разрешённые переходы — чтобы интерфейс не дублировал
    правила, которые живут в сервисе."""
    return Справочник(
        виды=[{"код": c, "название": n} for c, n in service.KINDS],
        состояния=list(service.ПЕРЕХОДЫ),
        переходы={k: sorted(v) for k, v in service.ПЕРЕХОДЫ.items()},
    )


@router.get("")
async def api_search(
    holder: str | None = None,
    state: str | None = None,
    kind: str | None = None,
    text: str = "",
    overdue: bool = False,
    mine: bool = False,
    limit: int = Query(default=200, ge=1, le=500),
    user: auth.User = Depends(auth.current_user),
) -> list[dict]:
    """Список документов. `mine=true` — то, что лежит на мне."""
    return await asyncio.to_thread(
        service.search,
        holder=user.login if mine else holder,
        state=state,
        kind=kind,
        text=text,
        overdue=overdue,
        limit=limit,
    )


@router.get("/timing")
async def api_timing(
    days: int = Query(default=90, ge=1, le=365), user: auth.User = Depends(auth.current_user)
) -> dict:
    """Сколько времени документы проводят на каждом участке.

    Та самая пустая строка «Время протекания процесса» из карты — только
    посчитанная из движений, а не собранная руками.

    Разбивка по сотрудникам — только администратору. То же решение, что на
    экране «Что происходит»: поимённый рейтинг медлительности, открытый всем
    тридцати, это наблюдение за коллегами, а не рабочая информация.
    """
    return await asyncio.to_thread(service.timing, days, with_people=bool(user.is_admin))


@router.get("/{doc_id}")
async def api_get(doc_id: int, user: auth.User = Depends(auth.current_user)) -> dict:
    try:
        return await asyncio.to_thread(service.get, doc_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail="Документ не найден") from e


@router.post("")
async def api_create(тело: НовыйДокумент, user: auth.User = Depends(auth.current_user)) -> dict:
    try:
        doc_id = await asyncio.to_thread(
            service.create,
            kind=тело.kind,
            number=тело.number,
            counterparty=тело.counterparty,
            subject=тело.subject,
            amount_kop=тело.amount_kop,
            due_at=тело.due_at,
            holder=тело.holder or user.login,
            author=user.login,
        )
    except service.FlowError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"id": doc_id}


@router.post("/{doc_id}/hand-over")
async def api_hand_over(
    doc_id: int, тело: Передача, user: auth.User = Depends(auth.current_user)
) -> dict:
    try:
        await asyncio.to_thread(
            service.hand_over, doc_id, to=тело.to, actor=user.login, note=тело.note
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail="Документ не найден") from e
    except service.FlowError as e:
        # 409, а не 400: запрос правильный, мешает текущее состояние документа.
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": True}


@router.post("/{doc_id}/state")
async def api_change_state(
    doc_id: int, тело: СменаСостояния, user: auth.User = Depends(auth.current_user)
) -> dict:
    try:
        await asyncio.to_thread(
            service.change_state, doc_id, to=тело.to, actor=user.login, note=тело.note
        )
    except LookupError as e:
        raise HTTPException(status_code=404, detail="Документ не найден") from e
    except service.FlowError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"ok": True}


class Люди(BaseModel):
    логины: list[str] = Field(default_factory=list)


@router.get("/reference/people")
async def api_people(user: auth.User = Depends(auth.current_user)) -> Люди:
    """Кому можно передать документ — список действующих учётных записей.

    Свободный ввод логина здесь недопустим: опечатка отправляет документ в
    несуществующего человека, и он пропадает из всех списков «у меня», оставаясь
    формально в работе.
    """
    from ..services import auth as auth_service

    люди = await asyncio.to_thread(auth_service.list_users)
    return Люди(логины=sorted(u["login"] for u in люди if not u["disabled"]))
