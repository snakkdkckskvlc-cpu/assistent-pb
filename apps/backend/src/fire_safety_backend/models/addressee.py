"""Pydantic-схемы для справочника адресатов."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Addressee(BaseModel):
    id: int
    name: str
    tone_hint: str = ""
    is_default: bool = False
    created_at: str


class AddresseeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    tone_hint: str = Field(default="", max_length=200)

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Название адресата не может быть пустым")
        return v

    @field_validator("tone_hint")
    @classmethod
    def _tone_hint_stripped(cls, v: str) -> str:
        return v.strip()
