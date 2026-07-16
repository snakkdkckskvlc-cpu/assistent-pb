"""Тонкий SQLite-слой на штатной либе (без ORM).

Файл БД: data/app.db в корне проекта. Схема поднимается и мигрирует
идемпотентно в `init_db()` — вызывается в lifespan FastAPI.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .. import config

DB_PATH = config.DATA_DIR / "app.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS addressees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    tone_hint TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

_DEFAULT_ADDRESSEES = [
    ("заказчик", "уважительно и по-деловому"),
    ("партнёр", "уважительно и по-деловому"),
    ("подрядчик", "по-деловому конкретно"),
    ("МЧС", "строго формально, со ссылкой на нормативку"),
    ("госорган", "строго формально, со ссылкой на нормативку"),
    ("ПАО НЛМК", "максимально формально, официальный тон"),
]


def init_db() -> None:
    """Создаёт таблицы и заливает дефолтных адресатов при первом запуске."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(_SCHEMA)
        for name, tone in _DEFAULT_ADDRESSEES:
            conn.execute(
                "INSERT OR IGNORE INTO addressees (name, tone_hint, is_default) VALUES (?, ?, 1)",
                (name, tone),
            )
