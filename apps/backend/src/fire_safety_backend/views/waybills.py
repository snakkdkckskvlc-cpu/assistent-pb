"""Роутер путевых листов и справочников к ним.

Роутер целиком под входом (main.py навешивает Depends на include_router):
справочник водителей содержит СНИЛС и номера водительских удостоверений, и
открытая ручка означала бы выдачу персональных данных всей внутренней сети.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException

from .. import config
from ..infrastructure import secure_files
from ..infrastructure.generators.waybill_docx import build_waybill_docx
from ..models.waybill import (
    Downtime,
    Driver,
    DriverCreate,
    DriverUpdate,
    Organization,
    OrganizationCreate,
    Trailer,
    TrailerCreate,
    Waybill,
    WaybillCreate,
    WaybillUpdate,
)
from ..services import ownership
from ..services import transport as transport_service
from ..services import waybills as service
from . import auth

router = APIRouter(prefix="/api/transport", tags=["waybills"])


# ── Организации ───────────────────────────────────────────────────────────


@router.get("/orgs")
async def list_orgs() -> list[Organization]:
    return await asyncio.to_thread(service.list_organizations)


@router.post("/orgs", status_code=201)
async def create_org(payload: OrganizationCreate) -> Organization:
    try:
        return await asyncio.to_thread(service.create_organization, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.put("/orgs/{org_id}")
async def update_org(org_id: int, payload: OrganizationCreate) -> Organization:
    try:
        return await asyncio.to_thread(service.update_organization, org_id, payload)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ── Водители ──────────────────────────────────────────────────────────────


@router.get("/drivers")
async def list_drivers(include_inactive: bool = False) -> list[Driver]:
    return await asyncio.to_thread(service.list_drivers, include_inactive=include_inactive)


@router.post("/drivers", status_code=201)
async def create_driver(payload: DriverCreate) -> Driver:
    try:
        return await asyncio.to_thread(service.create_driver, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.patch("/drivers/{driver_id}")
async def update_driver(driver_id: int, payload: DriverUpdate) -> Driver:
    try:
        return await asyncio.to_thread(service.update_driver, driver_id, payload)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/drivers/{driver_id}", status_code=204, response_model=None)
async def delete_driver(driver_id: int) -> None:
    try:
        await asyncio.to_thread(service.delete_driver, driver_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ── Прицепы ───────────────────────────────────────────────────────────────


@router.get("/trailers")
async def list_trailers(include_inactive: bool = False) -> list[Trailer]:
    return await asyncio.to_thread(service.list_trailers, include_inactive=include_inactive)


@router.post("/trailers", status_code=201)
async def create_trailer(payload: TrailerCreate) -> Trailer:
    try:
        return await asyncio.to_thread(service.create_trailer, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("/trailers/{trailer_id}", status_code=204, response_model=None)
async def delete_trailer(trailer_id: int) -> None:
    try:
        await asyncio.to_thread(service.delete_trailer, trailer_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


# ── Путевые листы ─────────────────────────────────────────────────────────


@router.get("/waybills")
async def list_waybills(vehicle_id: int | None = None, limit: int = 100) -> list[Waybill]:
    limit = max(1, min(limit, 500))
    return await asyncio.to_thread(service.list_waybills, vehicle_id=vehicle_id, limit=limit)


@router.get("/waybills/{waybill_id}")
async def get_waybill(waybill_id: int) -> Waybill:
    try:
        return await asyncio.to_thread(service.get_waybill, waybill_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/waybills", status_code=201)
async def create_waybill(
    payload: WaybillCreate, user: auth.User = Depends(auth.current_user)
) -> Waybill:
    try:
        return await asyncio.to_thread(service.create_waybill, payload, created_by=user.login)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.patch("/waybills/{waybill_id}")
async def update_waybill(waybill_id: int, payload: WaybillUpdate) -> Waybill:
    try:
        return await asyncio.to_thread(service.update_waybill, waybill_id, payload)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("/waybills/{waybill_id}", status_code=204, response_model=None)
async def delete_waybill(waybill_id: int) -> None:
    try:
        await asyncio.to_thread(service.delete_waybill, waybill_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.put("/waybills/{waybill_id}/trailers")
async def set_trailers(waybill_id: int, trailer_ids: list[int]) -> Waybill:
    try:
        return await asyncio.to_thread(service.set_trailers, waybill_id, trailer_ids)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.put("/waybills/{waybill_id}/downtimes")
async def set_downtimes(waybill_id: int, items: list[Downtime]) -> Waybill:
    try:
        return await asyncio.to_thread(service.set_downtimes, waybill_id, items)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/waybills/{waybill_id}/print")
async def print_waybill(waybill_id: int, user: auth.User = Depends(auth.current_user)) -> dict:
    """Собирает печатную форму листа — № 3 или 4-С по признаку в самом листе.

    Быстрая операция без модели, поэтому синхронно, мимо очереди задач — как
    и сборка письма (views/letter.py).
    """
    try:
        data = await asyncio.to_thread(service.print_data, waybill_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    trips = await asyncio.to_thread(transport_service.list_trips, waybill_id=waybill_id, limit=500)

    filename = f"waybill_{uuid.uuid4().hex[:12]}.docx"
    output_path = config.OUTPUT_DIR / filename
    try:
        # В листе фамилия водителя, номер удостоверения и СНИЛС — на диске он
        # лежит зашифрованным, как и остальные документы.
        with secure_files.encrypted_output(output_path) as writable:
            await asyncio.to_thread(
                build_waybill_docx, data, [t.model_dump() for t in trips], writable
            )
    except secure_files.StorageUnprotected as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось собрать бланк: {e}") from e

    await asyncio.to_thread(ownership.claim, filename, user.login)
    return {"docx_path": filename, "form": data.get("form", "3")}
