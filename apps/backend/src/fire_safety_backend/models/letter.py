"""Pydantic-модель запроса на генерацию письма."""
from __future__ import annotations

from pydantic import BaseModel, Field


class LetterRequest(BaseModel):
    draft: str = Field(min_length=1)
    # Свободная строка — берётся из справочника адресатов
    # (apps/backend/.../services/addressees.py). Пользователь может добавлять
    # новые типы прямо из UI, значения сохраняются в data/app.db.
    addressee_type: str = Field(default="заказчик", max_length=100)
