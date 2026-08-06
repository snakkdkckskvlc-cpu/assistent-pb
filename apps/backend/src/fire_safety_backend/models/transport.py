"""Pydantic-схемы модуля транспорта.

Наружу все величины идут в человеческих единицах — километрах и литрах, потому
что секретарь вводит «145,3 км», а не «145300 м». В БД они лежат целыми в
метрах и миллилитрах (см. infrastructure/db.py), пересчёт — на границе
сервиса. Округление до целого делается ОДИН раз, при записи; дальше всё
накопление идёт в целых, поэтому расхождения с бухгалтерией не набегает.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# Категории ТС из ПТС. Список закрытый: от категории зависят периодичность
# техосмотра и необходимость тахографа, и опечатка в ней тихо ломает расчёт.
VEHICLE_CATEGORIES = ("", "M1", "M2", "M3", "N1", "N2", "N3")

# Источник записи о рейсе. Пока пишет только человек; остальные два значения
# зарезервированы под подключение мониторинга и обмен с 1С.
TRIP_SOURCES = ("manual", "glonass", "1c")


class VehicleState(BaseModel):
    code: str
    title: str
    is_available: bool = True


class Vehicle(BaseModel):
    id: int
    call_name: str
    plate: str = ""
    brand: str = ""
    model: str = ""
    year: int | None = None
    category: str = ""
    fuel_type: str = ""
    tank_l: float | None = None
    odometer_km: float = 0.0
    state_code: str = "idle"
    state_title: str = ""
    has_glonass: bool = False
    tracker_id: str = ""
    # Литров на 100 км. None — норма не утверждена приказом, расход по норме
    # не считается (показывается только факт выдачи топлива).
    fuel_norm_l_100km: float | None = None
    notes: str = ""
    is_active: bool = True
    created_at: str
    # Открытый рейс: машина в поездке и выдать её второй раз нельзя.
    open_trip_id: int | None = None


class VehicleCreate(BaseModel):
    call_name: str = Field(min_length=1, max_length=60)
    plate: str = Field(default="", max_length=20)
    brand: str = Field(default="", max_length=40)
    model: str = Field(default="", max_length=40)
    year: int | None = Field(default=None, ge=1950, le=2100)
    category: str = Field(default="", max_length=4)
    fuel_type: str = Field(default="", max_length=20)
    tank_l: float | None = Field(default=None, gt=0, le=2000)
    odometer_km: float = Field(default=0.0, ge=0, le=10_000_000)
    has_glonass: bool = False
    tracker_id: str = Field(default="", max_length=64)
    fuel_norm_l_100km: float | None = Field(default=None, gt=0, le=200)
    notes: str = Field(default="", max_length=500)

    @field_validator("call_name")
    @classmethod
    def _call_name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Название машины не может быть пустым")
        return v

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in VEHICLE_CATEGORIES:
            allowed = ", ".join(c for c in VEHICLE_CATEGORIES if c)
            raise ValueError(f"Категория ТС должна быть одной из: {allowed}")
        return v

    @field_validator("plate", "brand", "model", "fuel_type", "tracker_id", "notes")
    @classmethod
    def _stripped(cls, v: str) -> str:
        return v.strip()


class VehicleUpdate(BaseModel):
    """Частичное обновление: присланы только изменяемые поля.

    Отдельная модель, а не VehicleCreate с необязательными полями: у None здесь
    смысл «не трогать», а в Create — «значение не задано», и слить их значило бы
    затирать пробег машины при правке одной заметки.
    """

    plate: str | None = Field(default=None, max_length=20)
    brand: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=40)
    year: int | None = Field(default=None, ge=1950, le=2100)
    category: str | None = Field(default=None, max_length=4)
    fuel_type: str | None = Field(default=None, max_length=20)
    tank_l: float | None = Field(default=None, gt=0, le=2000)
    odometer_km: float | None = Field(default=None, ge=0, le=10_000_000)
    state_code: str | None = None
    has_glonass: bool | None = None
    tracker_id: str | None = Field(default=None, max_length=64)
    fuel_norm_l_100km: float | None = Field(default=None, gt=0, le=200)
    notes: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None

    @field_validator("category")
    @classmethod
    def _known_category(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().upper()
        if v not in VEHICLE_CATEGORIES:
            allowed = ", ".join(c for c in VEHICLE_CATEGORIES if c)
            raise ValueError(f"Категория ТС должна быть одной из: {allowed}")
        return v


class Place(BaseModel):
    id: int
    name: str
    address: str = ""
    lat: float | None = None
    lon: float | None = None
    distance_from_base_km: float | None = None
    is_base: bool = False
    created_at: str


class PlaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    address: str = Field(default="", max_length=200)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    distance_from_base_km: float | None = Field(default=None, ge=0, le=20_000)
    is_base: bool = False

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Название точки не может быть пустым")
        return v


class Trip(BaseModel):
    id: int
    vehicle_id: int
    vehicle_name: str = ""
    driver: str = ""
    place_from_id: int | None = None
    place_from_name: str = ""
    place_to_id: int | None = None
    place_to_name: str = ""
    destination_text: str = ""
    purpose: str = ""
    departed_at: str
    returned_at: str | None = None
    odometer_start_km: float | None = None
    odometer_end_km: float | None = None
    distance_km: float | None = None
    fuel_issued_l: float = 0.0
    # Расход по утверждённой норме. None, пока норма по машине не задана
    # приказом: выдумывать её из паспортных данных нельзя — по ней списывают
    # топливо, и цифра должна быть той же, что в бухгалтерии.
    fuel_by_norm_l: float | None = None
    source: str = "manual"
    notes: str = ""
    created_by: str = ""
    created_at: str


class TripCreate(BaseModel):
    """Выдача машины: «беру Логан 145, до ТЭЦ-2»."""

    vehicle_id: int
    driver: str = Field(default="", max_length=100)
    place_from_id: int | None = None
    place_to_id: int | None = None
    destination_text: str = Field(default="", max_length=200)
    purpose: str = Field(default="", max_length=200)
    odometer_start_km: float | None = Field(default=None, ge=0, le=10_000_000)
    fuel_issued_l: float = Field(default=0.0, ge=0, le=2000)
    notes: str = Field(default="", max_length=500)

    @field_validator("driver", "destination_text", "purpose", "notes")
    @classmethod
    def _stripped(cls, v: str) -> str:
        return v.strip()


class TripClose(BaseModel):
    """Возврат машины: показание одометра и, если доливали, топливо."""

    odometer_end_km: float | None = Field(default=None, ge=0, le=10_000_000)
    fuel_issued_l: float | None = Field(default=None, ge=0, le=2000)
    notes: str | None = Field(default=None, max_length=500)
