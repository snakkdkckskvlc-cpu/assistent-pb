"""Тонкий SQLite-слой на штатной либе (без ORM).

Файл БД: data/app.db в корне проекта. Схема поднимается идемпотентно
в `init_db()` — вызывается в lifespan FastAPI. Доменные данные (сиды)
заливаются отдельно сервисами (см. services/addressees.py::seed_defaults). /
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

from .. import config

if TYPE_CHECKING:
    from collections.abc import Iterator

DB_PATH = config.DATA_DIR / "app.db"


def _casefold_collation(a: str, b: str) -> int:
    """Регистронезависимое сравнение с поддержкой не-ASCII (кириллица).

    Встроенная в SQLite коллация NOCASE фолдит только ASCII A-Z/a-z и
    не работает для кириллицы — «Дубликат» и «дубликат» считались бы
    разными строками. str.casefold() работает корректно для Unicode.
    """
    fa, fb = a.casefold(), b.casefold()
    return (fa > fb) - (fa < fb)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.create_collation("NOCASE_UNICODE", _casefold_collation)
    conn.execute("PRAGMA foreign_keys = ON")
    # БД трогают и event-loop поток (letter pipeline), и threadpool
    # (CRUD-роуты) — WAL + busy_timeout снижают риск "database is locked".
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# COLLATE NOCASE_UNICODE на name — регистронезависимая уникальность (в т.ч.
# для кириллицы), совпадает с сортировкой в list_all() и со сравнением в
# get_tone_hint(). Применяется только к новым БД (CREATE TABLE IF NOT EXISTS
# не мигрирует существующие) — для локальной pre-release БД это ожидаемо.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS addressees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE_UNICODE,
    tone_hint TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    function TEXT NOT NULL,
    task_id TEXT NOT NULL,
    rating TEXT NOT NULL,
    comment TEXT NOT NULL DEFAULT '',
    -- Что именно модель выдала, когда пользователь нажал 👎. Без этого
    -- комментарий «плохо разобрал ответственность» ни к чему не привязан и
    -- разбирать его через месяц не по чему.
    bad_output TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    duration_sec REAL,
    tokens INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    error TEXT
);
"""


# Столбцы, добавленные к уже существующим таблицам. CREATE TABLE IF NOT EXISTS
# существующую таблицу НЕ трогает, поэтому у пользователей с рабочей базой
# новые поля появятся только через ALTER TABLE.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    (
        "feedback",
        "bad_output",
        "ALTER TABLE feedback ADD COLUMN bad_output TEXT NOT NULL DEFAULT ''",
    ),
)


def _apply_migrations(conn) -> None:
    for table, column, statement in _MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(statement)


def init_db() -> None:
    """Создаёт таблицы (идемпотентно). Доменные сиды — см. seed_defaults()."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(_SCHEMA)
        _apply_migrations(conn)
