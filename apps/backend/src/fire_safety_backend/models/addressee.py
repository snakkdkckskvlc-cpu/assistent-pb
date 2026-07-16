"""Pydantic-схемы для справочника адресатов."""
from __future__ import annotations

from pydantic import BaseModel, Field


class Addressee(BaseModel):
    id: int
    name: str
    tone_hint: str = ""
    is_default: bool = False
    created_at: str


class AddresseeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    tone_hint: str = Field(default="", max_length=200)
