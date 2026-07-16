"""Роутер справочника адресатов."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import Addressee, AddresseeCreate
from ..services import addressees as service

router = APIRouter(prefix="/api/addressees", tags=["addressees"])


@router.get("")
def list_addressees() -> list[Addressee]:
    return service.list_all()


@router.post("", status_code=201)
def create_addressee(payload: AddresseeCreate) -> Addressee:
    try:
        return service.create(payload)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.delete("/{addressee_id}", status_code=204)
def delete_addressee(addressee_id: int) -> None:
    try:
        service.delete(addressee_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
