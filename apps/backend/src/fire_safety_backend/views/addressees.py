"""Роутер справочника адресатов."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from ..models import Addressee, AddresseeCreate
from ..services import addressees as service

router = APIRouter(prefix="/api/addressees", tags=["addressees"])


@router.get("")
async def list_addressees() -> list[Addressee]:
    return await asyncio.to_thread(service.list_all)


@router.post("", status_code=201)
async def create_addressee(payload: AddresseeCreate) -> Addressee:
    try:
        return await asyncio.to_thread(service.create, payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.delete("/{addressee_id}", status_code=204, response_model=None)
async def delete_addressee(addressee_id: int) -> None:
    try:
        await asyncio.to_thread(service.delete, addressee_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
