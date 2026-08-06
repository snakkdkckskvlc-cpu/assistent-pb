"""Сервис путевых листов и справочников к ним: организации, водители, прицепы.

Закрывает обе типовые межотраслевые формы Госкомстата — № 3 (легковой,
ОКУД 0345001) и № 4-С (грузовой, ОКУД 0345004). Заполняется всё, что в бумажном
бланке заполняет человек, кроме подписей.

Единицы: наружу километры, литры, рубли и минуты; в БД целые метры, миллилитры
и копейки. Пересчёт — таблицей _CONVERTED ниже, ровно в одном месте.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..infrastructure.db import connect
from ..models.waybill import (
    Downtime,
    Driver,
    DriverCreate,
    DriverUpdate,
    Organization,
    OrganizationCreate,
    Trailer,
    TrailerCreate,
    Waybill,
    WaybillCreate,
    WaybillUpdate,
)
from .units import (
    bool_to_int,
    km_to_m,
    kop_to_rub,
    l_to_ml,
    m_to_km,
    ml_to_l,
    rub_to_kop,
    x100_to_float,
    x100_to_int,
)

# Обе организации компании — реквизиты взяты из действующих бланков. Заливаются
# при первом запуске, дальше правятся в приложении: ОКПО в бланках не заполнен,
# и подставить его неоткуда.
_DEFAULT_ORGS: tuple[tuple[str, str, str, str, int], ...] = (
    (
        "ООО «ПожСервис»",
        "398005, г. Липецк, ул. Парковая, д. 10",
        "(4742) 28-67-88",
        "1054800315184",
        1,
    ),
    (
        "ООО «ПожМастер»",
        "г. Липецк, ул. Парковая, д. 10",
        "(4742) 28-67-88",
        "",
        0,
    ),
)


def seed_defaults() -> None:
    """Заливает организации, если их ещё нет (идемпотентно)."""
    with connect() as conn:
        for name, address, phone, ogrn, is_default in _DEFAULT_ORGS:
            conn.execute(
                "INSERT OR IGNORE INTO organization (name, address, phone, ogrn, is_default) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, address, phone, ogrn, is_default),
            )


# ── Организации ───────────────────────────────────────────────────────────


def list_organizations() -> list[Organization]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM organization ORDER BY is_default DESC, name COLLATE NOCASE_UNICODE"
        ).fetchall()
    return [_row_to_org(r) for r in rows]


def create_organization(payload: OrganizationCreate) -> Organization:
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO organization (name, address, phone, okpo, ogrn, is_default) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    payload.name,
                    payload.address,
                    payload.phone,
                    payload.okpo,
                    payload.ogrn,
                    int(payload.is_default),
                ),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"Организация «{payload.name}» уже заведена") from e
        new_id = cur.lastrowid
        if payload.is_default:
            conn.execute("UPDATE organization SET is_default = 0 WHERE id != ?", (new_id,))
        row = conn.execute("SELECT * FROM organization WHERE id = ?", (new_id,)).fetchone()
    return _row_to_org(row)


def update_organization(org_id: int, payload: OrganizationCreate) -> Organization:
    with connect() as conn:
        cur = conn.execute(
            "UPDATE organization SET name = ?, address = ?, phone = ?, okpo = ?, ogrn = ?, "
            "is_default = ? WHERE id = ?",
            (
                payload.name,
                payload.address,
                payload.phone,
                payload.okpo,
                payload.ogrn,
                int(payload.is_default),
                org_id,
            ),
        )
        if cur.rowcount == 0:
            raise LookupError(f"Организация id={org_id} не найдена")
        if payload.is_default:
            conn.execute("UPDATE organization SET is_default = 0 WHERE id != ?", (org_id,))
        row = conn.execute("SELECT * FROM organization WHERE id = ?", (org_id,)).fetchone()
    return _row_to_org(row)


def _row_to_org(row: sqlite3.Row) -> Organization:
    return Organization(
        id=row["id"],
        name=row["name"],
        address=row["address"] or "",
        phone=row["phone"] or "",
        okpo=row["okpo"] or "",
        ogrn=row["ogrn"] or "",
        is_default=bool(row["is_default"]),
        created_at=row["created_at"],
    )


# ── Водители ──────────────────────────────────────────────────────────────
# Таблица содержит СНИЛС и номер водительского удостоверения — обязательные
# реквизиты путевого листа. Роутер целиком под входом, в общих списках эти
# поля не показываются.


def list_drivers(*, include_inactive: bool = False) -> list[Driver]:
    where = "" if include_inactive else "WHERE is_active = 1"
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM driver {where} ORDER BY full_name COLLATE NOCASE_UNICODE"
        ).fetchall()
    return [_row_to_driver(r) for r in rows]


def create_driver(payload: DriverCreate) -> Driver:
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO driver (full_name, tab_number, licence_series, licence_number, "
                "licence_issued_at, licence_class, snils, licence_card) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload.full_name,
                    payload.tab_number,
                    payload.licence_series,
                    payload.licence_number,
                    payload.licence_issued_at,
                    payload.licence_class,
                    payload.snils,
                    payload.licence_card,
                ),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"Водитель «{payload.full_name}» уже заведён") from e
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM driver WHERE id = ?", (new_id,)).fetchone()
    return _row_to_driver(row)


_DRIVER_COLUMNS = (
    "full_name",
    "tab_number",
    "licence_series",
    "licence_number",
    "licence_issued_at",
    "licence_class",
    "snils",
    "licence_card",
    "is_active",
)


def update_driver(driver_id: int, payload: DriverUpdate) -> Driver:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return get_driver(driver_id)
    sets, values = [], []
    for field, value in changes.items():
        if field not in _DRIVER_COLUMNS:
            continue
        sets.append(f"{field} = ?")
        values.append(int(value) if field == "is_active" else value)
    if not sets:
        return get_driver(driver_id)
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE driver SET {', '.join(sets)} WHERE id = ?", (*values, driver_id)
        )
        if cur.rowcount == 0:
            raise LookupError(f"Водитель id={driver_id} не найден")
    return get_driver(driver_id)


def get_driver(driver_id: int) -> Driver:
    with connect() as conn:
        row = conn.execute("SELECT * FROM driver WHERE id = ?", (driver_id,)).fetchone()
    if row is None:
        raise LookupError(f"Водитель id={driver_id} не найден")
    return _row_to_driver(row)


def delete_driver(driver_id: int) -> None:
    """Убирает водителя. С выписанными листами — только пометкой неактивным.

    Путевой лист ссылается на водителя, и удаление строки лишило бы прошлый
    лист фамилии, номера удостоверения и СНИЛС — то есть обязательных
    реквизитов документа, который уже сдан.
    """
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM driver WHERE id = ?", (driver_id,)).fetchone()
        if row is None:
            raise LookupError(f"Водитель id={driver_id} не найден")
        used = conn.execute(
            "SELECT 1 FROM waybill WHERE driver_id = ? LIMIT 1", (driver_id,)
        ).fetchone()
        if used:
            conn.execute("UPDATE driver SET is_active = 0 WHERE id = ?", (driver_id,))
        else:
            conn.execute(
                "UPDATE vehicle SET default_driver_id = NULL WHERE default_driver_id = ?",
                (driver_id,),
            )
            conn.execute("DELETE FROM driver WHERE id = ?", (driver_id,))


def _row_to_driver(row: sqlite3.Row) -> Driver:
    return Driver(
        id=row["id"],
        full_name=row["full_name"],
        tab_number=row["tab_number"] or "",
        licence_series=row["licence_series"] or "",
        licence_number=row["licence_number"] or "",
        licence_issued_at=row["licence_issued_at"],
        licence_class=row["licence_class"] or "",
        snils=row["snils"] or "",
        licence_card=row["licence_card"] or "",
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
    )


# ── Прицепы ───────────────────────────────────────────────────────────────


def list_trailers(*, include_inactive: bool = False) -> list[Trailer]:
    where = "" if include_inactive else "WHERE is_active = 1"
    with connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM trailer {where} ORDER BY reg_number COLLATE NOCASE_UNICODE"
        ).fetchall()
    return [_row_to_trailer(r) for r in rows]


def create_trailer(payload: TrailerCreate) -> Trailer:
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO trailer (mark, reg_number, series, code) VALUES (?, ?, ?, ?)",
                (payload.mark, payload.reg_number, payload.series, payload.code),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"Прицеп «{payload.reg_number}» уже заведён") from e
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM trailer WHERE id = ?", (new_id,)).fetchone()
    return _row_to_trailer(row)


def delete_trailer(trailer_id: int) -> None:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM trailer WHERE id = ?", (trailer_id,)).fetchone()
        if row is None:
            raise LookupError(f"Прицеп id={trailer_id} не найден")
        used = conn.execute(
            "SELECT 1 FROM waybill_trailer WHERE trailer_id = ? LIMIT 1", (trailer_id,)
        ).fetchone()
        if used:
            conn.execute("UPDATE trailer SET is_active = 0 WHERE id = ?", (trailer_id,))
        else:
            conn.execute("DELETE FROM trailer WHERE id = ?", (trailer_id,))


def _row_to_trailer(row: sqlite3.Row) -> Trailer:
    return Trailer(
        id=row["id"],
        mark=row["mark"] or "",
        reg_number=row["reg_number"],
        series=row["series"] or "",
        code=row["code"] or "",
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
    )


# ── Путевой лист ──────────────────────────────────────────────────────────

# Поля модели, у которых имя и единица совпадают со столбцом БД.
_PLAIN: tuple[str, ...] = (
    "form",
    "org_id",
    "series",
    "number",
    "date_from",
    "date_to",
    "vehicle_id",
    "driver_id",
    "column_number",
    "brigade",
    "work_mode",
    "escorts",
    "communication_type",
    "transport_type",
    "customer_name",
    "customer_address",
    "pickup_address",
    "delivery_address",
    "cargo_name",
    "planned_arrival_at",
    "planned_trips",
    "planned_cargo_kg",
    "scheduled_departure_at",
    "scheduled_return_at",
    "departure_at",
    "return_at",
    "fuel_brand",
    "fuel_code",
    "fuel_sheet_number",
    "equipment_time_min",
    "engine_time_min",
    "medical_pre_mark",
    "medical_pre_at",
    "medical_pre_position",
    "medical_pre_name",
    "medical_post_mark",
    "medical_post_at",
    "medical_post_position",
    "medical_post_name",
    "tech_control_mark",
    "tech_control_at",
    "tech_control_name",
    "mechanic_name",
    "dispatcher_name",
    "accepted_by_driver",
    "return_condition",
    "vehicle_handed_by",
    "vehicle_taken_by",
    "total_time_min",
    "driving_time_min",
    "idle_vehicle_min",
    "idle_loading_min",
    "idle_repair_min",
    "idle_extra_min",
    "trips_done",
    "stops_done",
    "cargo_done_kg",
    "days_in_work",
    "ttd_count",
    "ttd_handed_by",
    "ttd_taken_by",
    "taxation_text",
    "taxer_name",
    "calc_position",
    "calc_name",
    "special_marks",
    "owner_marks",
    "delay_notes",
    "status",
)

# Поля, у которых наружу человеческая единица, а в БД целая мелкая:
# поле модели → (столбец, в БД, из БД).
_CONVERTED: dict[str, tuple[str, Any, Any]] = {
    "planned_distance_km": ("planned_distance_m", km_to_m, m_to_km),
    "fuel_to_issue_l": ("fuel_to_issue_ml", l_to_ml, ml_to_l),
    "odometer_start_km": ("odometer_start_m", km_to_m, m_to_km),
    "odometer_end_km": ("odometer_end_m", km_to_m, m_to_km),
    "zero_run_start_km": ("zero_run_start_m", km_to_m, m_to_km),
    "zero_run_end_km": ("zero_run_end_m", km_to_m, m_to_km),
    "fuel_start_l": ("fuel_start_ml", l_to_ml, ml_to_l),
    "fuel_issued_l": ("fuel_issued_ml", l_to_ml, ml_to_l),
    "fuel_end_l": ("fuel_end_ml", l_to_ml, ml_to_l),
    "fuel_returned_l": ("fuel_returned_ml", l_to_ml, ml_to_l),
    "fuel_norm_l_100km": ("fuel_norm_hs_x100", x100_to_int, x100_to_float),
    "fuel_coeff_equipment": ("fuel_coeff_equipment_x100", x100_to_int, x100_to_float),
    "fuel_coeff_engine": ("fuel_coeff_engine_x100", x100_to_int, x100_to_float),
    "licence_checked": ("licence_checked", bool_to_int, bool),
    "run_total_km": ("run_total_m", km_to_m, m_to_km),
    "run_loaded_km": ("run_loaded_m", km_to_m, m_to_km),
    "run_trailer_km": ("run_trailer_m", km_to_m, m_to_km),
    "cargo_tkm": ("cargo_tkm_x100", x100_to_int, x100_to_float),
    "fuel_used_norm_l": ("fuel_used_norm_ml", l_to_ml, ml_to_l),
    "fuel_used_fact_l": ("fuel_used_fact_ml", l_to_ml, ml_to_l),
    "salary_km_rub": ("salary_km_kop", rub_to_kop, kop_to_rub),
    "salary_hours_rub": ("salary_hours_kop", rub_to_kop, kop_to_rub),
    "salary_vehicle_rub": ("salary_vehicle_kop", rub_to_kop, kop_to_rub),
    "salary_trailer_rub": ("salary_trailer_kop", rub_to_kop, kop_to_rub),
    "salary_total_rub": ("salary_total_kop", rub_to_kop, kop_to_rub),
}

_WAYBILL_SELECT = """
SELECT w.*, o.name AS org_name,
       v.call_name AS vehicle_name, v.plate AS vehicle_plate,
       v.brand AS vehicle_brand, v.model AS vehicle_model,
       v.garage_number AS garage_number,
       d.full_name AS driver_name, d.tab_number AS driver_tab_number
  FROM waybill w
  LEFT JOIN organization o ON o.id = w.org_id
  JOIN vehicle v ON v.id = w.vehicle_id
  LEFT JOIN driver d ON d.id = w.driver_id
"""


def list_waybills(*, vehicle_id: int | None = None, limit: int = 100) -> list[Waybill]:
    where, params = "", []
    if vehicle_id is not None:
        where = "WHERE w.vehicle_id = ?"
        params.append(vehicle_id)
    with connect() as conn:
        rows = conn.execute(
            f"{_WAYBILL_SELECT} {where} ORDER BY w.date_from DESC, w.id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        # Прицепы и простои тянутся одним запросом на страницу, а не по одному
        # на лист: при сотне листов это сто лишних round-trip'ов на открытие
        # журнала.
        ids = [r["id"] for r in rows]
        trailers = _trailers_by_waybill(conn, ids)
        downtimes = _downtimes_by_waybill(conn, ids)
    return [_row_to_waybill(r, trailers.get(r["id"], []), downtimes.get(r["id"], [])) for r in rows]


def get_waybill(waybill_id: int) -> Waybill:
    with connect() as conn:
        row = conn.execute(f"{_WAYBILL_SELECT} WHERE w.id = ?", (waybill_id,)).fetchone()
        if row is None:
            raise LookupError(f"Путевой лист id={waybill_id} не найден")
        trailers = _trailers_by_waybill(conn, [waybill_id])
        downtimes = _downtimes_by_waybill(conn, [waybill_id])
    return _row_to_waybill(row, trailers.get(waybill_id, []), downtimes.get(waybill_id, []))


def create_waybill(payload: WaybillCreate, *, created_by: str = "") -> Waybill:
    """Заводит лист, подставляя из карточки машины всё, что там уже есть.

    Подстановка идёт только в НЕзаполненные поля: если секретарь явно указал
    другую организацию или другого водителя, карточка машины не должна это
    затирать.
    """
    sent = payload.model_dump(exclude_unset=True)
    with connect() as conn:
        vehicle = conn.execute(
            "SELECT * FROM vehicle WHERE id = ?", (payload.vehicle_id,)
        ).fetchone()
        if vehicle is None:
            raise LookupError(f"Машина id={payload.vehicle_id} не найдена")

        values = _to_columns(payload.model_dump())
        for field, column, source in (
            ("org_id", "org_id", vehicle["org_id"]),
            ("driver_id", "driver_id", vehicle["default_driver_id"]),
            ("form", "form", vehicle["waybill_form"]),
            ("fuel_code", "fuel_code", vehicle["fuel_code"]),
            ("fuel_brand", "fuel_brand", vehicle["fuel_type"]),
            ("column_number", "column_number", vehicle["column_number"]),
            ("brigade", "brigade", vehicle["brigade"]),
        ):
            if field not in sent and source:
                values[column] = source

        # Норма — снимком: приказ поменяет её, а закрытый лист обязан
        # пересчитываться по той цифре, по которой списали топливо.
        if "fuel_norm_l_100km" not in sent:
            values["fuel_norm_hs_x100"] = vehicle["fuel_norm_hs_x100"]
        if "odometer_start_km" not in sent:
            values["odometer_start_m"] = vehicle["odometer_m"]

        values["created_by"] = created_by
        columns = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        try:
            cur = conn.execute(
                f"INSERT INTO waybill ({columns}) VALUES ({marks})", tuple(values.values())
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"Путевой лист № {payload.number} этой серии уже выписан") from e
        new_id = cur.lastrowid
    return get_waybill(new_id)


def update_waybill(waybill_id: int, payload: WaybillUpdate) -> Waybill:
    changes = payload.model_dump(exclude_unset=True)
    values = _to_columns(changes)
    if not values:
        return get_waybill(waybill_id)
    sets = ", ".join(f"{column} = ?" for column in values)
    with connect() as conn:
        try:
            cur = conn.execute(
                f"UPDATE waybill SET {sets} WHERE id = ?", (*values.values(), waybill_id)
            )
        except sqlite3.IntegrityError as e:
            raise ValueError("Путевой лист с таким номером и серией уже выписан") from e
        if cur.rowcount == 0:
            raise LookupError(f"Путевой лист id={waybill_id} не найден")
    return get_waybill(waybill_id)


def delete_waybill(waybill_id: int) -> None:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM waybill WHERE id = ?", (waybill_id,)).fetchone()
        if row is None:
            raise LookupError(f"Путевой лист id={waybill_id} не найден")
        # Рейсы переживают лист: поездка была, даже если лист выписали ошибочно.
        # Отвязка руками, а не через ON DELETE SET NULL, — чтобы поведение
        # совпадало и на базе, созданной до появления этой связи (там столбец
        # добавлен через ALTER TABLE, где правило удаления не задать).
        conn.execute("UPDATE trip SET waybill_id = NULL WHERE waybill_id = ?", (waybill_id,))
        conn.execute("DELETE FROM waybill WHERE id = ?", (waybill_id,))


def set_trailers(waybill_id: int, trailer_ids: list[int]) -> Waybill:
    """Прицепляет к листу до четырёх прицепов — по числу мест в бланке 4-С."""
    if len(trailer_ids) > 4:
        raise ValueError("В бланке формы 4-С места только на четыре прицепа")
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM waybill WHERE id = ?", (waybill_id,)).fetchone()
        if row is None:
            raise LookupError(f"Путевой лист id={waybill_id} не найден")
        conn.execute("DELETE FROM waybill_trailer WHERE waybill_id = ?", (waybill_id,))
        for slot, trailer_id in enumerate(trailer_ids, start=1):
            try:
                conn.execute(
                    "INSERT INTO waybill_trailer (waybill_id, slot, trailer_id) VALUES (?, ?, ?)",
                    (waybill_id, slot, trailer_id),
                )
            except sqlite3.IntegrityError as e:
                raise ValueError(f"Прицеп id={trailer_id} не найден в справочнике") from e
    return get_waybill(waybill_id)


def set_downtimes(waybill_id: int, items: list[Downtime]) -> Waybill:
    """Переписывает список простоев целиком — так же, как правится бумажный бланк."""
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM waybill WHERE id = ?", (waybill_id,)).fetchone()
        if row is None:
            raise LookupError(f"Путевой лист id={waybill_id} не найден")
        conn.execute("DELETE FROM downtime WHERE waybill_id = ?", (waybill_id,))
        for item in items:
            conn.execute(
                "INSERT INTO downtime (waybill_id, name, reason, started_at, ended_at, "
                "responsible_name) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    waybill_id,
                    item.name,
                    item.reason,
                    item.started_at,
                    item.ended_at,
                    item.responsible_name,
                ),
            )
    return get_waybill(waybill_id)


def _to_columns(fields: dict[str, Any]) -> dict[str, Any]:
    """Поля модели → столбцы БД с пересчётом единиц."""
    out: dict[str, Any] = {}
    for field, value in fields.items():
        if field in _CONVERTED:
            column, to_db, _ = _CONVERTED[field]
            out[column] = None if value is None else to_db(value)
        elif field in _PLAIN:
            out[field] = value
    return out


def _trailers_by_waybill(
    conn: sqlite3.Connection, waybill_ids: list[int]
) -> dict[int, list[Trailer]]:
    if not waybill_ids:
        return {}
    marks = ", ".join("?" for _ in waybill_ids)
    rows = conn.execute(
        f"SELECT wt.waybill_id, t.* FROM waybill_trailer wt "
        f"JOIN trailer t ON t.id = wt.trailer_id "
        f"WHERE wt.waybill_id IN ({marks}) ORDER BY wt.slot",
        tuple(waybill_ids),
    ).fetchall()
    out: dict[int, list[Trailer]] = {}
    for row in rows:
        out.setdefault(row["waybill_id"], []).append(_row_to_trailer(row))
    return out


def _downtimes_by_waybill(
    conn: sqlite3.Connection, waybill_ids: list[int]
) -> dict[int, list[Downtime]]:
    if not waybill_ids:
        return {}
    marks = ", ".join("?" for _ in waybill_ids)
    rows = conn.execute(
        f"SELECT * FROM downtime WHERE waybill_id IN ({marks}) ORDER BY id", tuple(waybill_ids)
    ).fetchall()
    out: dict[int, list[Downtime]] = {}
    for row in rows:
        out.setdefault(row["waybill_id"], []).append(
            Downtime(
                id=row["id"],
                name=row["name"] or "",
                reason=row["reason"] or "",
                started_at=row["started_at"],
                ended_at=row["ended_at"],
                responsible_name=row["responsible_name"] or "",
            )
        )
    return out


def _fuel_figures(row: sqlite3.Row) -> tuple[float | None, float | None]:
    """Расход по цепочке бланка и экономия против нормы.

    Расход фактически = остаток при выезде + выдано − остаток при возвращении −
    сдано. Если хотя бы одного из остатков нет, считать нечего: полученная из
    трёх слагаемых цифра выглядела бы как факт, но фактом не была бы.

    Коэффициенты изменения нормы в расчёт НЕ входят: их размер и порядок
    применения задаёт приказ организации, которого ещё нет. Введённые значения
    сохраняются и печатаются, но расход по норме считается по базовой норме —
    иначе цифра разошлась бы с той, по которой бухгалтерия списала топливо.
    """
    fact_ml = row["fuel_used_fact_ml"]
    if fact_ml is None:
        parts = (
            row["fuel_start_ml"],
            row["fuel_issued_ml"],
            row["fuel_end_ml"],
        )
        if all(p is not None for p in parts):
            fact_ml = (
                row["fuel_start_ml"]
                + row["fuel_issued_ml"]
                - row["fuel_end_ml"]
                - (row["fuel_returned_ml"] or 0)
            )

    norm_ml = row["fuel_used_norm_ml"]
    if norm_ml is None:
        run_m = row["run_total_m"]
        if (
            run_m is None
            and row["odometer_start_m"] is not None
            and row["odometer_end_m"] is not None
        ):
            run_m = row["odometer_end_m"] - row["odometer_start_m"]
        if run_m and row["fuel_norm_hs_x100"]:
            norm_ml = round(run_m / 1000 * row["fuel_norm_hs_x100"] / 100 * 10)

    saving_ml = None if fact_ml is None or norm_ml is None else norm_ml - fact_ml
    return ml_to_l(fact_ml), ml_to_l(saving_ml)


def _row_to_waybill(
    row: sqlite3.Row, trailers: list[Trailer], downtimes: list[Downtime]
) -> Waybill:
    data: dict[str, Any] = {}
    for field in _PLAIN:
        data[field] = row[field]
    for field, (column, _, from_db) in _CONVERTED.items():
        value = row[column]
        data[field] = None if value is None else from_db(value)

    fact_l, saving_l = _fuel_figures(row)
    mark = " ".join(x for x in (row["vehicle_brand"], row["vehicle_model"]) if x)
    return Waybill(
        id=row["id"],
        org_name=row["org_name"] or "",
        vehicle_name=row["vehicle_name"] or "",
        vehicle_plate=row["vehicle_plate"] or "",
        vehicle_mark=mark,
        garage_number=row["garage_number"] or "",
        driver_name=row["driver_name"] or "",
        driver_tab_number=row["driver_tab_number"] or "",
        fuel_balance_l=fact_l,
        fuel_saving_l=saving_l,
        trailers=trailers,
        downtimes=downtimes,
        created_by=row["created_by"] or "",
        created_at=row["created_at"],
        **{k: v for k, v in data.items() if v is not None},
    )
