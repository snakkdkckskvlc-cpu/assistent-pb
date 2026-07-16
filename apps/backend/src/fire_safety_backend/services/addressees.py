"""Сервис справочника адресатов писем: список/создание/удаление.

Хранение — SQLite (infrastructure/db.py). Никакой бизнес-логики LLM здесь нет:
сервис работает только с БД.
"""

from __future__ import annotations

import sqlite3

from ..infrastructure.db import connect
from ..models import Addressee, AddresseeCreate

# Дефолтные адресаты, заливаются при первом запуске (init_db → seed_defaults).
# «ПАО НЛМК» — реальный основной заказчик компании, оставлен как пример.
_DEFAULT_ADDRESSEES = [
    ("заказчик", "уважительно и по-деловому"),
    ("партнёр", "уважительно и по-деловому"),
    ("подрядчик", "по-деловому конкретно"),
    ("МЧС", "строго формально, со ссылкой на нормативку"),
    ("госорган", "строго формально, со ссылкой на нормативку"),
    ("ПАО НЛМК", "максимально формально, официальный тон"),
]


def seed_defaults() -> None:
    """Заливает дефолтных адресатов, если их ещё нет (идемпотентно)."""
    with connect() as conn:
        for name, tone in _DEFAULT_ADDRESSEES:
            conn.execute(
                "INSERT OR IGNORE INTO addressees (name, tone_hint, is_default) VALUES (?, ?, 1)",
                (name, tone),
            )


def list_all() -> list[Addressee]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, name, tone_hint, is_default, created_at "
            "FROM addressees ORDER BY is_default DESC, name COLLATE NOCASE_UNICODE"
        ).fetchall()
    return [_row_to_model(r) for r in rows]


def create(payload: AddresseeCreate) -> Addressee:
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO addressees (name, tone_hint, is_default) VALUES (?, ?, 0)",
                (payload.name, payload.tone_hint),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"Адресат «{payload.name}» уже существует") from e
        new_id = cur.lastrowid
        row = conn.execute(
            "SELECT id, name, tone_hint, is_default, created_at FROM addressees WHERE id = ?",
            (new_id,),
        ).fetchone()
    return _row_to_model(row)


def delete(addressee_id: int) -> None:
    """Удаляет пользовательского адресата. Дефолтные (is_default=1) не удаляются."""
    with connect() as conn:
        row = conn.execute(
            "SELECT is_default FROM addressees WHERE id = ?", (addressee_id,)
        ).fetchone()
        if row is None:
            raise LookupError(f"Адресат id={addressee_id} не найден")
        if row["is_default"]:
            raise PermissionError("Нельзя удалить встроенный тип адресата")
        conn.execute("DELETE FROM addressees WHERE id = ?", (addressee_id,))


def get_tone_hint(name: str) -> str:
    """Возвращает подсказку тона для адресата (используется в промпте письма).

    Сравнение регистронезависимое и без учёта краевых пробелов — совпадает
    с сортировкой в list_all() и с уникальностью в схеме БД.
    Если адресат неизвестен — возвращает пустую строку (модель сама решит).
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT tone_hint FROM addressees WHERE name = ? COLLATE NOCASE_UNICODE",
            (name.strip(),),
        ).fetchone()
    return row["tone_hint"] if row else ""


def _row_to_model(row: sqlite3.Row) -> Addressee:
    return Addressee(
        id=row["id"],
        name=row["name"],
        tone_hint=row["tone_hint"] or "",
        is_default=bool(row["is_default"]),
        created_at=row["created_at"],
    )
