"""Роутер учёта транспорта: машины, точки, рейсы.

Каркас: реализовано то, что не зависит от ответов сотрудника. Не хватает и
ждёт разговора (docs/02-product/transport-checklist.md):
  * печать путевого листа — неизвестен порядок предрейсового медосмотра,
    без отметки о нём лист недействителен (раздел 3);
  * обмен с 1С — неизвестна конфигурация и есть ли доступ к базе (раздел 4);
  * подтягивание пробега из ГЛОНАСС — неизвестна платформа мониторинга
    (раздел 2);
  * коэффициенты к норме расхода — нет приказа директора (раздел 1);
  * карта маршрутов — нет списка постоянных точек (раздел 5).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from ..models.transport import (
    Place,
    PlaceCreate,
    Trip,
    TripClose,
    TripCreate,
    Vehicle,
    VehicleCreate,
    VehicleState,
    VehicleUpdate,
)
from ..services import transport as service
from . import auth

router = APIRouter(prefix="/api/transport", tags=["transport"])


@router.get("/states")
async def list_states() -> list[VehicleState]:
    return await asyncio.to_thread(service.list_states)


# ── Машины ────────────────────────────────────────────────────────────────


@router.get("/vehicles")
async def list_vehicles(include_inactive: bool = False) -> list[Vehicle]:
    return await asyncio.to_thread(service.list_vehicles, include_inactive=include_inactive)


@router.get("/vehicles/{vehicle_id}")
async def get_vehicle(vehicle_id: int) -> Vehicle:
    try:
        return await asyncio.to_thread(service.get_vehicle, vehicle_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/vehicles", status_code=201)
async def create_vehicle(payload: VehicleCreate) -> Vehicle:
    try:
        return await asyncio.to_thread(service.create_vehicle, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.patch("/vehicles/{vehicle_id}")
async def update_vehicle(vehicle_id: int, payload: VehicleUpdate) -> Vehicle:
    try:
        return await asyncio.to_thread(service.update_vehicle, vehicle_id, payload)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.delete("/vehicles/{vehicle_id}", status_code=204, response_model=None)
async def delete_vehicle(vehicle_id: int) -> None:
    try:
        await asyncio.to_thread(service.delete_vehicle, vehicle_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


# ── Точки ─────────────────────────────────────────────────────────────────


@router.get("/places")
async def list_places() -> list[Place]:
    return await asyncio.to_thread(service.list_places)


@router.post("/places", status_code=201)
async def create_place(payload: PlaceCreate) -> Place:
    try:
        return await asyncio.to_thread(service.create_place, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("/places/{place_id}", status_code=204, response_model=None)
async def delete_place(place_id: int) -> None:
    try:
        await asyncio.to_thread(service.delete_place, place_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ── Рейсы ─────────────────────────────────────────────────────────────────


@router.get("/trips")
async def list_trips(vehicle_id: int | None = None, limit: int = 100) -> list[Trip]:
    limit = max(1, min(limit, 500))
    return await asyncio.to_thread(service.list_trips, vehicle_id=vehicle_id, limit=limit)


@router.post("/trips", status_code=201)
async def open_trip(payload: TripCreate, user: auth.User = Depends(auth.current_user)) -> Trip:
    try:
        return await asyncio.to_thread(service.open_trip, payload, created_by=user.login)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.post("/trips/{trip_id}/close")
async def close_trip(trip_id: int, payload: TripClose) -> Trip:
    try:
        return await asyncio.to_thread(service.close_trip, trip_id, payload)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
