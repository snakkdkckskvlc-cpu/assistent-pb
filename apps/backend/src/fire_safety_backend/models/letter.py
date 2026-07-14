"""Pydantic-модель запроса на генерацию письма."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class LetterRequest(BaseModel):
    draft: str
    addressee_type: Literal[
        "заказчик", "МЧС", "госорган", "партнёр", "подрядчик"
    ] = "заказчик"
