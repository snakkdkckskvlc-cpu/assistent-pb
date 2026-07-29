"""Сервис фидбека по результатам (👍/👎): только запись, без бизнес-логики.

Хранение — SQLite (infrastructure/db.py), как и services/addressees.py.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..infrastructure.db import connect

if TYPE_CHECKING:
    from ..models import FeedbackCreate

# Потолок на сохраняемый ответ модели. Юр. анализ договора — это десятки
# находок с цитатами, целиком он занимает сотни килобайт, и класть такое в
# базу отзывов незачем: для разбора хватает начала, а по task_id при
# необходимости поднимается всё остальное.
_MAX_BAD_OUTPUT_CHARS = 20000


def _serialize_output(result: object) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            text = str(result)
    if len(text) > _MAX_BAD_OUTPUT_CHARS:
        text = text[:_MAX_BAD_OUTPUT_CHARS] + "\n… (обрезано)"
    return text


def create(payload: FeedbackCreate, bad_output: object = None) -> None:
    """Записывает отзыв.

    bad_output сохраняется только для «👎 с комментарием»: связка «что
    пользователь считает неправильным» + «что модель на самом деле выдала» —
    единственное, из чего потом можно собрать негативные примеры для промпта.
    Для 👍 и для 👎 без пояснения хранить ответ модели незачем — разбирать в
    нём нечего, а базу он раздует.
    """
    keep_output = payload.rating == "down" and bool(payload.comment.strip())
    with connect() as conn:
        conn.execute(
            "INSERT INTO feedback (function, task_id, rating, comment, bad_output) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                payload.function,
                payload.task_id,
                payload.rating,
                payload.comment,
                _serialize_output(bad_output) if keep_output else "",
            ),
        )


def list_negative(days: int = 30, function: str | None = None) -> list[dict]:
    """Отзывы 👎 с пояснением и сохранённым ответом модели за последние N дней.

    Используется scripts/update_prompts_from_feedback.py. Без пояснения отзыв
    бесполезен: «плохо» не превратить в правило для промпта.
    """
    query = (
        "SELECT id, created_at, function, task_id, comment, bad_output FROM feedback "
        "WHERE rating = 'down' AND TRIM(comment) <> '' "
        "AND created_at >= datetime('now', ?) "
    )
    params: list[object] = [f"-{int(days)} days"]
    if function:
        query += "AND function = ? "
        params.append(function)
    query += "ORDER BY id DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
