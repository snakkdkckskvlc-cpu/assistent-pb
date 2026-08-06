"""Сервис учёта транспорта: машины, точки, рейсы.

Каркас. Заполняется по ответам сотрудника (docs/02-product/
transport-checklist.md); места, где не хватает данных, помечены в коде.

Хранение — SQLite, никакой LLM здесь нет. Единицы: наружу километры и литры,
в БД целые метры и миллилитры (обоснование — в схеме infrastructure/db.py).
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..infrastructure.db import connect
from ..models.transport import (
    Place,
    PlaceCreate,
    Trip,
    TripClose,
    TripCreate,
    Vehicle,
    VehicleCreate,
    VehicleState,
    VehicleUpdate,
    waybill_form_for,
)
from .units import km_to_m, l_to_ml, m_to_km, ml_to_l, x100_to_float, x100_to_int

# Состояния машины. Заливаются при первом запуске; список правится в БД без
# выпуска новой версии — поэтому это сид, а не константа-перечисление.
# is_available=0 означает «выдать нельзя»: машина в рейсе, на ТО или списана.
_DEFAULT_STATES: tuple[tuple[str, str, int, int], ...] = (
    ("idle", "В гараже", 1, 10),
    ("on_trip", "В рейсе", 0, 20),
    ("maintenance", "На ТО", 0, 30),
    ("repair", "В ремонте", 0, 40),
    ("written_off", "Списана", 0, 50),
)

_STATE_IDLE = "idle"
_STATE_ON_TRIP = "on_trip"


def seed_defaults() -> None:
    """Заливает состояния машин, если их ещё нет (идемпотентно)."""
    with connect() as conn:
        for code, title, available, order in _DEFAULT_STATES:
            conn.execute(
                "INSERT OR IGNORE INTO vehicle_state (code, title, is_available, sort_order) "
                "VALUES (?, ?, ?, ?)",
                (code, title, available, order),
            )


# ── Состояния ─────────────────────────────────────────────────────────────


def list_states() -> list[VehicleState]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT code, title, is_available FROM vehicle_state ORDER BY sort_order, title"
        ).fetchall()
    return [
        VehicleState(code=r["code"], title=r["title"], is_available=bool(r["is_available"]))
        for r in rows
    ]


# ── Машины ────────────────────────────────────────────────────────────────

_VEHICLE_SELECT = """
SELECT v.*, s.title AS state_title, o.name AS org_name, d.full_name AS default_driver_name,
       (SELECT t.id FROM trip t
         WHERE t.vehicle_id = v.id AND t.returned_at IS NULL
         ORDER BY t.departed_at DESC LIMIT 1) AS open_trip_id
  FROM vehicle v
  LEFT JOIN vehicle_state s ON s.code = v.state_code
  LEFT JOIN organization o ON o.id = v.org_id
  LEFT JOIN driver d ON d.id = v.default_driver_id
"""


def list_vehicles(*, include_inactive: bool = False) -> list[Vehicle]:
    where = "" if include_inactive else "WHERE v.is_active = 1"
    with connect() as conn:
        rows = conn.execute(
            f"{_VEHICLE_SELECT} {where} ORDER BY v.call_name COLLATE NOCASE_UNICODE"
        ).fetchall()
    return [_row_to_vehicle(r) for r in rows]


def get_vehicle(vehicle_id: int) -> Vehicle:
    with connect() as conn:
        row = conn.execute(f"{_VEHICLE_SELECT} WHERE v.id = ?", (vehicle_id,)).fetchone()
    if row is None:
        raise LookupError(f"Машина id={vehicle_id} не найдена")
    return _row_to_vehicle(row)


def create_vehicle(payload: VehicleCreate) -> Vehicle:
    with connect() as conn:
        # Организаций две, и у большинства машин она одна и та же — берём
        # отмеченную основной, чтобы не заставлять выбирать её каждый раз.
        row = conn.execute("SELECT id FROM organization WHERE is_default = 1 LIMIT 1").fetchone()
        default_org = row["id"] if row else None
        try:
            cur = conn.execute(
                "INSERT INTO vehicle (call_name, plate, brand, model, year, category, "
                "fuel_type, tank_ml, odometer_m, state_code, has_glonass, tracker_id, "
                "fuel_norm_hs_x100, notes, org_id, waybill_form, garage_number, "
                "vehicle_code, fuel_code, column_number, brigade, default_driver_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload.call_name,
                    payload.plate,
                    payload.brand,
                    payload.model,
                    payload.year,
                    payload.category,
                    payload.fuel_type,
                    l_to_ml(payload.tank_l),
                    km_to_m(payload.odometer_km) or 0,
                    _STATE_IDLE,
                    int(payload.has_glonass),
                    payload.tracker_id,
                    x100_to_int(payload.fuel_norm_l_100km),
                    payload.notes,
                    payload.org_id or default_org,
                    payload.waybill_form or waybill_form_for(payload.category),
                    payload.garage_number,
                    payload.vehicle_code,
                    payload.fuel_code,
                    payload.column_number,
                    payload.brigade,
                    payload.default_driver_id,
                ),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"Машина «{payload.call_name}» уже заведена") from e
        new_id = cur.lastrowid
    return get_vehicle(new_id)


# Поле модели → (столбец, преобразователь). Явная таблица вместо setattr по
# именам: она не даёт случайно открыть на запись служебные столбцы, когда в
# VehicleUpdate добавится новое поле.
_VEHICLE_UPDATABLE: dict[str, tuple[str, Any]] = {
    "plate": ("plate", str),
    "brand": ("brand", str),
    "model": ("model", str),
    "year": ("year", int),
    "category": ("category", str),
    "fuel_type": ("fuel_type", str),
    "tank_l": ("tank_ml", l_to_ml),
    "odometer_km": ("odometer_m", km_to_m),
    "state_code": ("state_code", str),
    "has_glonass": ("has_glonass", int),
    "tracker_id": ("tracker_id", str),
    "fuel_norm_l_100km": ("fuel_norm_hs_x100", x100_to_int),
    "notes": ("notes", str),
    "is_active": ("is_active", int),
    "org_id": ("org_id", int),
    "waybill_form": ("waybill_form", str),
    "garage_number": ("garage_number", str),
    "vehicle_code": ("vehicle_code", str),
    "fuel_code": ("fuel_code", str),
    "column_number": ("column_number", str),
    "brigade": ("brigade", str),
    "default_driver_id": ("default_driver_id", int),
}


def update_vehicle(vehicle_id: int, payload: VehicleUpdate) -> Vehicle:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return get_vehicle(vehicle_id)

    sets, values = [], []
    for field, value in changes.items():
        column, convert = _VEHICLE_UPDATABLE[field]
        sets.append(f"{column} = ?")
        values.append(None if value is None else convert(value))

    with connect() as conn:
        if "state_code" in changes:
            known = conn.execute(
                "SELECT 1 FROM vehicle_state WHERE code = ?", (changes["state_code"],)
            ).fetchone()
            if known is None:
                raise ValueError(f"Неизвестное состояние «{changes['state_code']}»")
        cur = conn.execute(
            f"UPDATE vehicle SET {', '.join(sets)} WHERE id = ?", (*values, vehicle_id)
        )
        if cur.rowcount == 0:
            raise LookupError(f"Машина id={vehicle_id} не найдена")
    return get_vehicle(vehicle_id)


def delete_vehicle(vehicle_id: int) -> None:
    """Убирает машину из списка.

    Машина с рейсами не удаляется, а помечается неактивной: удаление утащило бы
    за собой (ON DELETE CASCADE) историю поездок и топлива, по которой уже
    отчитались. Из выдачи она пропадает одинаково в обоих случаях.
    """
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM vehicle WHERE id = ?", (vehicle_id,)).fetchone()
        if row is None:
            raise LookupError(f"Машина id={vehicle_id} не найдена")
        # Машина в рейсе не убирается: иначе рейс остался бы незакрытым
        # навсегда, а машина при этом исчезла бы из списка возвратов.
        on_trip = conn.execute(
            "SELECT 1 FROM trip WHERE vehicle_id = ? AND returned_at IS NULL LIMIT 1",
            (vehicle_id,),
        ).fetchone()
        if on_trip:
            raise ValueError("Машина в рейсе — сначала отметьте возврат")
        has_trips = conn.execute(
            "SELECT 1 FROM trip WHERE vehicle_id = ? LIMIT 1", (vehicle_id,)
        ).fetchone()
        if has_trips:
            conn.execute("UPDATE vehicle SET is_active = 0 WHERE id = ?", (vehicle_id,))
        else:
            conn.execute("DELETE FROM vehicle WHERE id = ?", (vehicle_id,))


def _row_to_vehicle(row: sqlite3.Row) -> Vehicle:
    return Vehicle(
        id=row["id"],
        call_name=row["call_name"],
        plate=row["plate"] or "",
        brand=row["brand"] or "",
        model=row["model"] or "",
        year=row["year"],
        category=row["category"] or "",
        fuel_type=row["fuel_type"] or "",
        tank_l=ml_to_l(row["tank_ml"]),
        odometer_km=m_to_km(row["odometer_m"]) or 0.0,
        state_code=row["state_code"],
        state_title=row["state_title"] or row["state_code"],
        has_glonass=bool(row["has_glonass"]),
        tracker_id=row["tracker_id"] or "",
        fuel_norm_l_100km=x100_to_float(row["fuel_norm_hs_x100"]),
        notes=row["notes"] or "",
        is_active=bool(row["is_active"]),
        created_at=row["created_at"],
        open_trip_id=row["open_trip_id"],
        org_id=row["org_id"],
        org_name=row["org_name"] or "",
        waybill_form=row["waybill_form"] or "3",
        garage_number=row["garage_number"] or "",
        vehicle_code=row["vehicle_code"] or "",
        fuel_code=row["fuel_code"] or "",
        column_number=row["column_number"] or "",
        brigade=row["brigade"] or "",
        default_driver_id=row["default_driver_id"],
        default_driver_name=row["default_driver_name"] or "",
    )


# ── Точки ─────────────────────────────────────────────────────────────────


def list_places() -> list[Place]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM place ORDER BY is_base DESC, name COLLATE NOCASE_UNICODE"
        ).fetchall()
    return [_row_to_place(r) for r in rows]


def create_place(payload: PlaceCreate) -> Place:
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO place (name, address, lat, lon, distance_from_base_m, is_base) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    payload.name,
                    payload.address,
                    payload.lat,
                    payload.lon,
                    km_to_m(payload.distance_from_base_km),
                    int(payload.is_base),
                ),
            )
        except sqlite3.IntegrityError as e:
            raise ValueError(f"Точка «{payload.name}» уже есть в справочнике") from e
        new_id = cur.lastrowid
        # База ровно одна: расстояния в справочнике считаются от неё, и две
        # базы сделали бы цифру километража неоднозначной.
        if payload.is_base:
            conn.execute("UPDATE place SET is_base = 0 WHERE id != ?", (new_id,))
        row = conn.execute("SELECT * FROM place WHERE id = ?", (new_id,)).fetchone()
    return _row_to_place(row)


def delete_place(place_id: int) -> None:
    with connect() as conn:
        row = conn.execute("SELECT 1 FROM place WHERE id = ?", (place_id,)).fetchone()
        if row is None:
            raise LookupError(f"Точка id={place_id} не найдена")
        # Ссылки в рейсах обнуляются, а не блокируют удаление: название точки
        # у рейса уже сохранено в destination_text при выдаче машины.
        conn.execute("UPDATE trip SET place_from_id = NULL WHERE place_from_id = ?", (place_id,))
        conn.execute("UPDATE trip SET place_to_id = NULL WHERE place_to_id = ?", (place_id,))
        conn.execute("DELETE FROM place WHERE id = ?", (place_id,))


def _row_to_place(row: sqlite3.Row) -> Place:
    return Place(
        id=row["id"],
        name=row["name"],
        address=row["address"] or "",
        lat=row["lat"],
        lon=row["lon"],
        distance_from_base_km=m_to_km(row["distance_from_base_m"]),
        is_base=bool(row["is_base"]),
        created_at=row["created_at"],
    )


# ── Рейсы ─────────────────────────────────────────────────────────────────

_TRIP_SELECT = """
SELECT t.*, v.call_name AS vehicle_name,
       pf.name AS place_from_name, pt.name AS place_to_name
  FROM trip t
  JOIN vehicle v ON v.id = t.vehicle_id
  LEFT JOIN place pf ON pf.id = t.place_from_id
  LEFT JOIN place pt ON pt.id = t.place_to_id
"""


def list_trips(
    *, vehicle_id: int | None = None, waybill_id: int | None = None, limit: int = 100
) -> list[Trip]:
    clauses, params = [], []
    if vehicle_id is not None:
        clauses.append("t.vehicle_id = ?")
        params.append(vehicle_id)
    if waybill_id is not None:
        clauses.append("t.waybill_id = ?")
        params.append(waybill_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    # Рейсы одного листа — это его оборотная сторона, и там они идут по
    # номеру ездки, а не от новых к старым.
    order = "t.seq, t.id" if waybill_id is not None else "t.departed_at DESC, t.id DESC"
    with connect() as conn:
        rows = conn.execute(
            f"{_TRIP_SELECT} {where} ORDER BY {order} LIMIT ?", (*params, limit)
        ).fetchall()
    return [_row_to_trip(r) for r in rows]


def open_trip(payload: TripCreate, *, created_by: str = "") -> Trip:
    """Выдаёт машину в рейс.

    Одометр на выезде можно не вводить: подставляется последнее известное
    показание машины. Заставлять секретаря идти к машине за цифрой ради
    записи — верный способ получить учёт мимо программы.
    """
    with connect() as conn:
        vehicle = conn.execute(
            "SELECT id, odometer_m, fuel_norm_hs_x100, is_active FROM vehicle WHERE id = ?",
            (payload.vehicle_id,),
        ).fetchone()
        if vehicle is None:
            raise LookupError(f"Машина id={payload.vehicle_id} не найдена")
        if not vehicle["is_active"]:
            raise ValueError("Машина выведена из списка — выдать её нельзя")

        already = conn.execute(
            "SELECT id FROM trip WHERE vehicle_id = ? AND returned_at IS NULL LIMIT 1",
            (payload.vehicle_id,),
        ).fetchone()
        if already:
            raise ValueError("Машина уже в рейсе — сначала отметьте возврат")

        odometer_start = km_to_m(payload.odometer_start_km)
        if odometer_start is None:
            odometer_start = vehicle["odometer_m"]

        destination = payload.destination_text
        if not destination and payload.place_to_id is not None:
            point = conn.execute(
                "SELECT name FROM place WHERE id = ?", (payload.place_to_id,)
            ).fetchone()
            if point is not None:
                destination = point["name"]

        # Номер ездки в листе: продолжаем нумерацию того же листа, а не
        # заводим свою — на обороте бланка эти номера идут подряд.
        seq = payload.seq
        if seq is None and payload.waybill_id is not None:
            row = conn.execute(
                "SELECT MAX(seq) AS last FROM trip WHERE waybill_id = ?",
                (payload.waybill_id,),
            ).fetchone()
            seq = (row["last"] or 0) + 1

        cur = conn.execute(
            "INSERT INTO trip (vehicle_id, driver, place_from_id, place_to_id, "
            "destination_text, purpose, odometer_start_m, fuel_issued_ml, "
            "fuel_norm_hs_x100, source, notes, created_by, waybill_id, seq, "
            "customer_code, user_name, consignor, ttd_numbers, trailer_in, "
            "trailer_out, empty_run_m, cargo_weight_kg) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload.vehicle_id,
                payload.driver,
                payload.place_from_id,
                payload.place_to_id,
                destination,
                payload.purpose,
                odometer_start,
                l_to_ml(payload.fuel_issued_l) or 0,
                vehicle["fuel_norm_hs_x100"],
                payload.notes,
                created_by,
                payload.waybill_id,
                seq,
                payload.customer_code,
                payload.user_name,
                payload.consignor,
                payload.ttd_numbers,
                payload.trailer_in,
                payload.trailer_out,
                km_to_m(payload.empty_run_km),
                payload.cargo_weight_kg,
            ),
        )
        trip_id = cur.lastrowid
        conn.execute(
            "UPDATE vehicle SET state_code = ? WHERE id = ?",
            (_STATE_ON_TRIP, payload.vehicle_id),
        )
    return get_trip(trip_id)


def close_trip(trip_id: int, payload: TripClose) -> Trip:
    """Отмечает возврат машины и переносит одометр в карточку."""
    with connect() as conn:
        row = conn.execute(
            "SELECT vehicle_id, returned_at, odometer_start_m, fuel_issued_ml "
            "FROM trip WHERE id = ?",
            (trip_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f"Рейс id={trip_id} не найден")
        if row["returned_at"] is not None:
            raise ValueError("Рейс уже закрыт")

        odometer_end = km_to_m(payload.odometer_end_km)
        if (
            odometer_end is not None
            and row["odometer_start_m"] is not None
            and odometer_end < row["odometer_start_m"]
        ):
            raise ValueError(
                "Показание одометра на возврате меньше, чем на выезде — проверьте цифру"
            )

        fuel = row["fuel_issued_ml"]
        if payload.fuel_issued_l is not None:
            fuel = l_to_ml(payload.fuel_issued_l)

        conn.execute(
            "UPDATE trip SET returned_at = CURRENT_TIMESTAMP, odometer_end_m = ?, "
            "fuel_issued_ml = ?, notes = COALESCE(?, notes) WHERE id = ?",
            (odometer_end, fuel, payload.notes, trip_id),
        )
        if odometer_end is not None:
            # max() — на случай, если пробег машины уже поправили вручную,
            # пока она была в рейсе: откатывать одометр назад нельзя.
            conn.execute(
                "UPDATE vehicle SET odometer_m = MAX(odometer_m, ?) WHERE id = ?",
                (odometer_end, row["vehicle_id"]),
            )
        conn.execute(
            "UPDATE vehicle SET state_code = ? WHERE id = ? AND state_code = ?",
            (_STATE_IDLE, row["vehicle_id"], _STATE_ON_TRIP),
        )
    return get_trip(trip_id)


def get_trip(trip_id: int) -> Trip:
    with connect() as conn:
        row = conn.execute(f"{_TRIP_SELECT} WHERE t.id = ?", (trip_id,)).fetchone()
    if row is None:
        raise LookupError(f"Рейс id={trip_id} не найден")
    return _row_to_trip(row)


def _row_to_trip(row: sqlite3.Row) -> Trip:
    start, end = row["odometer_start_m"], row["odometer_end_m"]
    distance_m = end - start if start is not None and end is not None else None

    # Расход по норме считается ТОЛЬКО когда норма утверждена и пробег известен.
    # Никаких «примерно по паспорту»: по этой цифре списывают топливо, и она
    # обязана сходиться с той, что уйдёт в бухгалтерию. Берётся снимок нормы из
    # самого рейса, а не текущая по машине, — иначе новый приказ задним числом
    # переписал бы уже закрытый месяц.
    norm = row["fuel_norm_hs_x100"]
    fuel_by_norm_l = None
    if norm and distance_m:
        # Зимняя надбавка и городской коэффициент сюда пока НЕ входят: их
        # размер утверждается приказом организации, которого ещё нет
        # (docs/02-product/transport-checklist.md, раздел 1). Когда появится —
        # коэффициент домножается здесь и так же снимком пишется в рейс.
        fuel_by_norm_l = round(distance_m / 1000 * norm / 100 / 100, 2)

    return Trip(
        id=row["id"],
        vehicle_id=row["vehicle_id"],
        vehicle_name=row["vehicle_name"] or "",
        driver=row["driver"] or "",
        place_from_id=row["place_from_id"],
        place_from_name=row["place_from_name"] or "",
        place_to_id=row["place_to_id"],
        place_to_name=row["place_to_name"] or "",
        destination_text=row["destination_text"] or "",
        purpose=row["purpose"] or "",
        departed_at=row["departed_at"],
        returned_at=row["returned_at"],
        odometer_start_km=m_to_km(start),
        odometer_end_km=m_to_km(end),
        distance_km=m_to_km(distance_m),
        fuel_issued_l=ml_to_l(row["fuel_issued_ml"]) or 0.0,
        fuel_by_norm_l=fuel_by_norm_l,
        source=row["source"] or "manual",
        notes=row["notes"] or "",
        created_by=row["created_by"] or "",
        created_at=row["created_at"],
        waybill_id=row["waybill_id"],
        seq=row["seq"],
        customer_code=row["customer_code"] or "",
        user_name=row["user_name"] or "",
        consignor=row["consignor"] or "",
        ttd_numbers=row["ttd_numbers"] or "",
        trailer_in=row["trailer_in"] or "",
        trailer_out=row["trailer_out"] or "",
        empty_run_km=m_to_km(row["empty_run_m"]),
        cargo_weight_kg=row["cargo_weight_kg"],
    )
