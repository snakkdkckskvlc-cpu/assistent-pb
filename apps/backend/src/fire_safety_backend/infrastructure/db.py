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

-- Учётные записи. Пароль хранится ТОЛЬКО как scrypt-хеш со своей солью:
-- восстановить его нельзя, при утечке базы перебор дорог (scrypt память-жёсткий).
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT NOT NULL UNIQUE COLLATE NOCASE_UNICODE,
    password_hash BLOB NOT NULL,
    salt BLOB NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    disabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Сессии в БД, а не подписанные cookie: так вход можно ОТОЗВАТЬ (выход,
-- отключение учётной записи), а подписанный токен живёт до истечения срока
-- и отозвать его нечем.
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

-- Кто чей файл в data/outputs. Отдельная таблица, а не колонка в
-- task_history: файл появляется в СЕРЕДИНЕ задачи, а запись в историю
-- делается после её завершения.
CREATE TABLE IF NOT EXISTS output_files (
    filename TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Результаты задач, чтобы перезапуск сервера не терял то, что человек ждал
-- минутами. ВАЖНО: result здесь — это разбор договора вместе с текстом
-- документа, поэтому он лежит ЗАШИФРОВАННЫМ (infrastructure/secure_files.py).
-- В task_history текст документов не пишется намеренно, и превращать app.db в
-- открытое хранилище договоров через эту таблицу нельзя.
CREATE TABLE IF NOT EXISTS task_results (
    task_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    result BLOB,
    error TEXT,
    progress TEXT NOT NULL DEFAULT '',
    percent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_task_results_owner ON task_results(owner);

-- ── Транспорт ─────────────────────────────────────────────────────────────
-- Модуль строится ручным вводом вперёд, а не вокруг трекера: ГЛОНАСС стоит на
-- 3 машинах из 12, и если сделать основой его, девять машин останутся без
-- учёта. Данные трекера — уточнение поверх ручной записи, поэтому все поля
-- заполнимы человеком, а признак источника хранится отдельно (trip.source).
--
-- Все величины — ЦЕЛЫЕ, в мелких единицах: расстояние в метрах (_m), топливо
-- в миллилитрах (_ml), деньги в копейках (_kop). Топливный учёт складывается
-- сотнями рейсов, и накопленная погрешность float'а превратилась бы в
-- расхождение с бухгалтерией, которое нечем объяснить. В человеческие
-- километры и литры пересчёт идёт на границе API (services/transport.py).

-- Состояние машины — таблица, а не константы в коде: перечень состояний в
-- автопарке меняется («на консервации», «передана в аренду»), и добавление
-- строки не должно требовать выпуска новой версии программы.
CREATE TABLE IF NOT EXISTS vehicle_state (
    code TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    is_available INTEGER NOT NULL DEFAULT 1,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vehicle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Как машину называют вслух: «Логан 145». Секретарь ищет её по этому
    -- имени, а не по госномеру, поэтому оно обязательное и уникальное.
    call_name TEXT NOT NULL UNIQUE COLLATE NOCASE_UNICODE,
    plate TEXT NOT NULL DEFAULT '',
    brand TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    year INTEGER,
    -- Категория из ПТС (M1, N1, N2, N3): от неё зависят периодичность
    -- техосмотра и нужен ли тахограф.
    category TEXT NOT NULL DEFAULT '',
    fuel_type TEXT NOT NULL DEFAULT '',
    tank_ml INTEGER,
    odometer_m INTEGER NOT NULL DEFAULT 0,
    state_code TEXT NOT NULL DEFAULT 'idle' REFERENCES vehicle_state(code),
    has_glonass INTEGER NOT NULL DEFAULT 0,
    -- Идентификатор машины в системе мониторинга. Пусто, пока не известно,
    -- что за платформа стоит у поставщика (см. docs/02-product/
    -- transport-checklist.md, раздел 2) — это и есть точка подключения.
    tracker_id TEXT NOT NULL DEFAULT '',
    -- Норма расхода, сотые доли литра на 100 км (7.8 л → 780). NULL, пока нет
    -- приказа директора, утверждающего нормы по парку: расход списывается не
    -- по паспортному значению, а по утверждённой норме на базе распоряжения
    -- Минтранса АМ-23-р. Пока NULL — расход по норме не считается вовсе,
    -- показывается только факт выдачи.
    fuel_norm_hs_x100 INTEGER,
    notes TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Постоянные точки: площадки НЛМК, ТЭЦ, склады, база. distance_from_base_m
-- заполняется вручную — справочник расстояний дешевле маршрутизатора, пока
-- точек десятки; координаты нужны, чтобы позже показать их на карте.
CREATE TABLE IF NOT EXISTS place (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE_UNICODE,
    address TEXT NOT NULL DEFAULT '',
    lat REAL,
    lon REAL,
    distance_from_base_m INTEGER,
    is_base INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Рейс — одна поездка «оттуда-туда». НЕ путевой лист: лист является
-- юридическим документом с отметками предрейсового медосмотра и техконтроля
-- выпуска, без которых он недействителен, и одним листом может закрываться
-- несколько рейсов. Таблица waybill появится отдельно, когда станет известно,
-- как медосмотр проводится в компании (transport-checklist.md, раздел 3);
-- ссылка trip.waybill_id добавится миграцией.
CREATE TABLE IF NOT EXISTS trip (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id INTEGER NOT NULL REFERENCES vehicle(id) ON DELETE CASCADE,
    driver TEXT NOT NULL DEFAULT '',
    place_from_id INTEGER REFERENCES place(id),
    place_to_id INTEGER REFERENCES place(id),
    -- Свободный текст на случай разовой точки, которой нет в справочнике:
    -- заставлять секретаря заводить точку ради одной поездки — верный способ
    -- получить учёт мимо программы.
    destination_text TEXT NOT NULL DEFAULT '',
    purpose TEXT NOT NULL DEFAULT '',
    departed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    returned_at TEXT,
    odometer_start_m INTEGER,
    odometer_end_m INTEGER,
    fuel_issued_ml INTEGER NOT NULL DEFAULT 0,
    -- Снимок нормы на момент рейса. Норма меняется приказом (зимняя надбавка,
    -- новый коэффициент), и без снимка пересчёт прошлого месяца дал бы цифры,
    -- отличные от тех, по которым уже списали топливо.
    fuel_norm_hs_x100 INTEGER,
    -- manual | glonass | 1c — откуда пришла запись. Ручной ввод остаётся
    -- основным; поле нужно, чтобы позже не гадать, какие рейсы можно
    -- перезаписывать данными трекера, а какие правил человек.
    source TEXT NOT NULL DEFAULT 'manual',
    notes TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trip_vehicle ON trip(vehicle_id, departed_at);
CREATE INDEX IF NOT EXISTS idx_trip_open ON trip(returned_at) WHERE returned_at IS NULL;
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
    (
        # Кто запускал задачу. Пусто у записей, сделанных до появления
        # разграничения доступа — такие видны всем вошедшим, иначе прежняя
        # история исчезла бы у своих же владельцев.
        "task_history",
        "owner",
        "ALTER TABLE task_history ADD COLUMN owner TEXT NOT NULL DEFAULT ''",
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
