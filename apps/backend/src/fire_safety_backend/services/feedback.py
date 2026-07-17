"""Сервис фидбека по результатам (👍/👎): только запись, без бизнес-логики.

Хранение — SQLite (infrastructure/db.py), как и services/addressees.py.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..infrastructure.db import connect

if TYPE_CHECKING:
    from ..models import FeedbackCreate


def create(payload: FeedbackCreate) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO feedback (function, task_id, rating, comment) VALUES (?, ?, ?, ?)",
            (payload.function, payload.task_id, payload.rating, payload.comment),
        )
