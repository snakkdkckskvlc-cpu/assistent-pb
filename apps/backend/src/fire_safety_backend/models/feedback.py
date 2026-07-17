"""Pydantic-схема для фидбека по результатам (👍/👎)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class FeedbackCreate(BaseModel):
    function: str = Field(min_length=1, max_length=50)
    task_id: str = Field(min_length=1, max_length=64)
    rating: Literal["up", "down"]
    comment: str = Field(default="", max_length=1000)

    @field_validator("comment")
    @classmethod
    def _comment_stripped(cls, v: str) -> str:
        return v.strip()
