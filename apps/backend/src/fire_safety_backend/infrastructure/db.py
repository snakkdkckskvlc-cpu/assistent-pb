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

-- Организаций две — ООО «ПожМастер» и ООО «ПожСервис». Наименование, адрес и
-- ОКПО печатаются в шапке путевого листа и относятся к обязательным
-- реквизитам, поэтому это справочник, а не константа: лист с чужим
-- наименованием юридически дефектен.
CREATE TABLE IF NOT EXISTS organization (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE_UNICODE,
    address TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    okpo TEXT NOT NULL DEFAULT '',
    ogrn TEXT NOT NULL DEFAULT '',
    is_default INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ВНИМАНИЕ: таблица содержит персональные данные (СНИЛС, номер водительского
-- удостоверения). Оба поля — обязательные реквизиты путевого листа, поэтому
-- хранение осознанное и согласовано. Практические следствия: доступ только
-- вошедшим (роутер под auth целиком), в общих списках эти поля не выводятся,
-- а диск должен быть зашифрован — состояние BitLocker показывает страница
-- «История задач».
CREATE TABLE IF NOT EXISTS driver (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL UNIQUE COLLATE NOCASE_UNICODE,
    tab_number TEXT NOT NULL DEFAULT '',
    licence_series TEXT NOT NULL DEFAULT '',
    licence_number TEXT NOT NULL DEFAULT '',
    licence_issued_at TEXT,
    licence_class TEXT NOT NULL DEFAULT '',
    snils TEXT NOT NULL DEFAULT '',
    -- «стандартная / ограниченная», в бланке ненужное зачёркивается
    licence_card TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Прицепы формы 4-С: в бланке их до четырёх, у каждого своя марка и
-- регистрационный знак.
CREATE TABLE IF NOT EXISTS trailer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mark TEXT NOT NULL DEFAULT '',
    reg_number TEXT NOT NULL UNIQUE COLLATE NOCASE_UNICODE,
    series TEXT NOT NULL DEFAULT '',
    code TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Владелец: организаций две, и в шапке листа печатается своя.
    org_id INTEGER REFERENCES organization(id),
    -- Какой бланк печатать: '3' — легковой, '4-S' — грузовой. Выводится из
    -- категории ПТС при заведении, но хранится отдельно: у грузовой машины,
    -- возящей только людей, бланк бывает другим, и правило «M1 значит № 3»
    -- ошибётся молча.
    waybill_form TEXT NOT NULL DEFAULT '3',
    garage_number TEXT NOT NULL DEFAULT '',
    -- «Коды марок: автомобиля» с оборотной стороны формы 4-С
    vehicle_code TEXT NOT NULL DEFAULT '',
    fuel_code TEXT NOT NULL DEFAULT '',
    column_number TEXT NOT NULL DEFAULT '',
    brigade TEXT NOT NULL DEFAULT '',
    -- Закреплённый водитель: подставляется в новый лист, но не запрещает
    -- выписать лист на другого.
    default_driver_id INTEGER REFERENCES driver(id)
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
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- Оборотная сторона путевого листа — это и есть список рейсов: в форме № 3
    -- «номер по порядку», в 4-С «номер ездки». Лист может быть не выписан
    -- (рейс записали на ходу), поэтому связь необязательная и при удалении
    -- листа рейс остаётся.
    waybill_id INTEGER REFERENCES waybill(id) ON DELETE SET NULL,
    seq INTEGER,
    customer_code TEXT NOT NULL DEFAULT '',
    -- Расшифровка подписи лица, пользовавшегося автомобилем (форма № 3)
    user_name TEXT NOT NULL DEFAULT '',
    -- Поля оборотной стороны формы 4-С
    consignor TEXT NOT NULL DEFAULT '',
    ttd_numbers TEXT NOT NULL DEFAULT '',
    trailer_in TEXT NOT NULL DEFAULT '',
    trailer_out TEXT NOT NULL DEFAULT '',
    empty_run_m INTEGER,
    cargo_weight_kg INTEGER
);

CREATE INDEX IF NOT EXISTS idx_trip_vehicle ON trip(vehicle_id, departed_at);
CREATE INDEX IF NOT EXISTS idx_trip_open ON trip(returned_at) WHERE returned_at IS NULL;

-- Путевой лист. ОДНА таблица на обе типовые формы Госкомстата — № 3 (легковой
-- автомобиль, ОКУД 0345001) и № 4-С (грузовой, ОКУД 0345004), различаются по
-- столбцу form. Совпадает у них процентов восемьдесят: шапка, водитель,
-- одометр, движение горючего, медосмотр, техконтроль. Две таблицы означали бы
-- два экземпляра одной и той же арифметики топлива и пробега — и расхождение
-- между ними при первой же правке. Поля, которых в форме № 3 нет (прицепы,
-- ткм, простои), у легкового листа просто остаются пустыми.
--
-- Подписи не хранятся — их ставят на бумаге. Расшифровки подписей (кто
-- именно) хранятся: это печатаемый текст бланка.
CREATE TABLE IF NOT EXISTS waybill (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Какие графы заполнила программа из карточки машины, а не человек.
    -- Список имён через запятую. Нужен интерфейсу: подставленное значение
    -- надо отличать от введённого, иначе человек не понимает, можно ли его
    -- трогать, и на всякий случай не трогает.
    autofilled TEXT NOT NULL DEFAULT '',
    form TEXT NOT NULL DEFAULT '3',
    org_id INTEGER REFERENCES organization(id),
    series TEXT NOT NULL DEFAULT '',
    number TEXT NOT NULL DEFAULT '',
    date_from TEXT NOT NULL,
    date_to TEXT NOT NULL,
    vehicle_id INTEGER NOT NULL REFERENCES vehicle(id) ON DELETE CASCADE,
    driver_id INTEGER REFERENCES driver(id),
    column_number TEXT NOT NULL DEFAULT '',
    brigade TEXT NOT NULL DEFAULT '',
    work_mode TEXT NOT NULL DEFAULT '',
    escorts TEXT NOT NULL DEFAULT '',

    -- Задание водителю
    communication_type TEXT NOT NULL DEFAULT '',
    transport_type TEXT NOT NULL DEFAULT '',
    customer_name TEXT NOT NULL DEFAULT '',
    customer_address TEXT NOT NULL DEFAULT '',
    pickup_address TEXT NOT NULL DEFAULT '',
    delivery_address TEXT NOT NULL DEFAULT '',
    cargo_name TEXT NOT NULL DEFAULT '',
    planned_arrival_at TEXT,
    planned_trips INTEGER,
    planned_distance_m INTEGER,
    planned_cargo_kg INTEGER,
    fuel_to_issue_ml INTEGER,

    -- Работа водителя и автомобиля: график против факта
    scheduled_departure_at TEXT,
    scheduled_return_at TEXT,
    departure_at TEXT,
    return_at TEXT,
    odometer_start_m INTEGER,
    odometer_end_m INTEGER,
    zero_run_start_m INTEGER,
    zero_run_end_m INTEGER,

    -- Движение горючего. Цепочка бланка: остаток при выезде + выдано −
    -- остаток при возвращении − сдано = расход фактически. Против него
    -- ставится расход по норме, разница даёт экономию или перерасход.
    fuel_brand TEXT NOT NULL DEFAULT '',
    fuel_code TEXT NOT NULL DEFAULT '',
    fuel_sheet_number TEXT NOT NULL DEFAULT '',
    fuel_start_ml INTEGER,
    fuel_issued_ml INTEGER,
    fuel_end_ml INTEGER,
    fuel_returned_ml INTEGER,
    -- Снимок нормы на момент листа: приказ меняет норму, и без снимка
    -- пересчёт закрытого месяца дал бы не те цифры, по которым списали.
    fuel_norm_hs_x100 INTEGER,
    -- «Коэффициент изменения нормы» — поле самого бланка, а не только
    -- приказа: зимняя надбавка и работа спецоборудования проставляются здесь.
    fuel_coeff_equipment_x100 INTEGER,
    fuel_coeff_engine_x100 INTEGER,
    equipment_time_min INTEGER,
    engine_time_min INTEGER,

    -- Медосмотр, техконтроль, приём-передача. Отметка — текст бланка
    -- («прошёл», «допущен»), а не флаг: пишут по-разному.
    medical_pre_mark TEXT NOT NULL DEFAULT '',
    medical_pre_at TEXT,
    medical_pre_position TEXT NOT NULL DEFAULT '',
    medical_pre_name TEXT NOT NULL DEFAULT '',
    medical_post_mark TEXT NOT NULL DEFAULT '',
    medical_post_at TEXT,
    medical_post_position TEXT NOT NULL DEFAULT '',
    medical_post_name TEXT NOT NULL DEFAULT '',
    tech_control_mark TEXT NOT NULL DEFAULT '',
    tech_control_at TEXT,
    tech_control_name TEXT NOT NULL DEFAULT '',
    licence_checked INTEGER NOT NULL DEFAULT 0,
    mechanic_name TEXT NOT NULL DEFAULT '',
    dispatcher_name TEXT NOT NULL DEFAULT '',
    accepted_by_driver TEXT NOT NULL DEFAULT '',
    return_condition TEXT NOT NULL DEFAULT '',
    vehicle_handed_by TEXT NOT NULL DEFAULT '',
    vehicle_taken_by TEXT NOT NULL DEFAULT '',

    -- Результаты работы за смену
    total_time_min INTEGER,
    driving_time_min INTEGER,
    idle_vehicle_min INTEGER,
    idle_loading_min INTEGER,
    idle_repair_min INTEGER,
    idle_extra_min INTEGER,
    trips_done INTEGER,
    stops_done INTEGER,
    run_total_m INTEGER,
    run_loaded_m INTEGER,
    run_trailer_m INTEGER,
    cargo_done_kg INTEGER,
    cargo_tkm_x100 INTEGER,
    days_in_work INTEGER,
    fuel_used_norm_ml INTEGER,
    fuel_used_fact_ml INTEGER,

    -- Товарно-транспортные документы и таксировка (форма 4-С)
    ttd_count INTEGER,
    ttd_handed_by TEXT NOT NULL DEFAULT '',
    ttd_taken_by TEXT NOT NULL DEFAULT '',
    taxation_text TEXT NOT NULL DEFAULT '',
    taxer_name TEXT NOT NULL DEFAULT '',

    -- Зарплата: в копейках, чтобы «руб. коп» бланка сходились без округлений
    salary_km_kop INTEGER,
    salary_hours_kop INTEGER,
    salary_vehicle_kop INTEGER,
    salary_trailer_kop INTEGER,
    salary_total_kop INTEGER,
    calc_position TEXT NOT NULL DEFAULT '',
    calc_name TEXT NOT NULL DEFAULT '',

    special_marks TEXT NOT NULL DEFAULT '',
    owner_marks TEXT NOT NULL DEFAULT '',
    delay_notes TEXT NOT NULL DEFAULT '',

    status TEXT NOT NULL DEFAULT 'draft',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_waybill_vehicle ON waybill(vehicle_id, date_from);
-- Номер уникален в пределах организации и серии, но только когда он уже
-- присвоен: черновики без номера не должны конфликтовать друг с другом.
CREATE UNIQUE INDEX IF NOT EXISTS idx_waybill_number
    ON waybill(org_id, series, number) WHERE number != '';

-- Прицепы, прицепленные к листу: в бланке 4-С четыре именованных места.
CREATE TABLE IF NOT EXISTS waybill_trailer (
    waybill_id INTEGER NOT NULL REFERENCES waybill(id) ON DELETE CASCADE,
    slot INTEGER NOT NULL,
    trailer_id INTEGER NOT NULL REFERENCES trailer(id),
    PRIMARY KEY (waybill_id, slot)
);

-- «Простои на линии» с оборотной стороны формы 4-С.
CREATE TABLE IF NOT EXISTS downtime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    waybill_id INTEGER NOT NULL REFERENCES waybill(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL DEFAULT '',
    started_at TEXT,
    ended_at TEXT,
    responsible_name TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_downtime_waybill ON downtime(waybill_id);
"""


# Столбцы, добавленные к уже существующим таблицам. CREATE TABLE IF NOT EXISTS
# существующую таблицу НЕ трогает, поэтому у пользователей с рабочей базой
# новые поля появятся только через ALTER TABLE.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    (
        # База с путевыми листами могла быть создана до появления пометок
        # автоподстановки. Старые листы получат пустое значение — это честно:
        # чем их заполняли, мы уже не знаем.
        "waybill",
        "autofilled",
        "ALTER TABLE waybill ADD COLUMN autofilled TEXT NOT NULL DEFAULT ''",
    ),
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
    # Поля, добавленные к транспорту, когда стали известны реальные бланки
    # путевых листов (формы № 3 и 4-С). База с транспортом уже могла быть
    # создана до этого, поэтому столбцы доезжают только через ALTER TABLE.
    *(
        ("vehicle", column, f"ALTER TABLE vehicle ADD COLUMN {ddl}")
        for column, ddl in (
            ("org_id", "org_id INTEGER REFERENCES organization(id)"),
            ("waybill_form", "waybill_form TEXT NOT NULL DEFAULT '3'"),
            ("garage_number", "garage_number TEXT NOT NULL DEFAULT ''"),
            ("vehicle_code", "vehicle_code TEXT NOT NULL DEFAULT ''"),
            ("fuel_code", "fuel_code TEXT NOT NULL DEFAULT ''"),
            ("column_number", "column_number TEXT NOT NULL DEFAULT ''"),
            ("brigade", "brigade TEXT NOT NULL DEFAULT ''"),
            ("default_driver_id", "default_driver_id INTEGER REFERENCES driver(id)"),
        )
    ),
    *(
        ("trip", column, f"ALTER TABLE trip ADD COLUMN {ddl}")
        for column, ddl in (
            ("waybill_id", "waybill_id INTEGER REFERENCES waybill(id)"),
            ("seq", "seq INTEGER"),
            ("customer_code", "customer_code TEXT NOT NULL DEFAULT ''"),
            ("user_name", "user_name TEXT NOT NULL DEFAULT ''"),
            ("consignor", "consignor TEXT NOT NULL DEFAULT ''"),
            ("ttd_numbers", "ttd_numbers TEXT NOT NULL DEFAULT ''"),
            ("trailer_in", "trailer_in TEXT NOT NULL DEFAULT ''"),
            ("trailer_out", "trailer_out TEXT NOT NULL DEFAULT ''"),
            ("empty_run_m", "empty_run_m INTEGER"),
            ("cargo_weight_kg", "cargo_weight_kg INTEGER"),
        )
    ),
)


# Индексы по столбцам, которые на существующей базе появляются только после
# ALTER TABLE. В _SCHEMA им не место: executescript идёт ДО миграций и упал бы
# на «no such column» у всех, кто уже запускал прошлую версию.
_POST_MIGRATION_SCHEMA = """
CREATE INDEX IF NOT EXISTS idx_trip_waybill ON trip(waybill_id, seq);
"""


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
        conn.executescript(_POST_MIGRATION_SCHEMA)
