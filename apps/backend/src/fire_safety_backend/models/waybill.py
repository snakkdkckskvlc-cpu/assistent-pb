"""Pydantic-схемы путевого листа, справочников организаций, водителей и прицепов.

Покрывают обе типовые межотраслевые формы Госкомстата: № 3 (легковой
автомобиль, ОКУД 0345001) и № 4-С (грузовой, ОКУД 0345004). В приложении
заполняется всё, что в бумажном бланке заполняется от руки, кроме самих
подписей — их ставят на распечатке. Расшифровки подписей (кто именно) это
печатаемый текст, и они здесь есть.

Единицы наружу человеческие: километры, литры, рубли, минуты. В БД — целые
метры, миллилитры и копейки (обоснование в схеме infrastructure/db.py).
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# Форма бланка. '3' — легковой автомобиль, '4-S' — грузовой.
# Латинская S намеренно: кириллическая «С» в коде выглядит неотличимо и
# однажды уже стоила бы часа на поиск «одинаковых» строк, которые не равны.
WAYBILL_FORMS = ("3", "4-S")

WAYBILL_STATUSES = ("draft", "issued", "closed")

LICENCE_CARDS = ("", "стандартная", "ограниченная")


class Organization(BaseModel):
    id: int
    name: str
    address: str = ""
    phone: str = ""
    okpo: str = ""
    ogrn: str = ""
    is_default: bool = False
    created_at: str


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(default="", max_length=300)
    phone: str = Field(default="", max_length=60)
    okpo: str = Field(default="", max_length=20)
    ogrn: str = Field(default="", max_length=20)
    is_default: bool = False

    @field_validator("name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Название организации не может быть пустым")
        return v


class Driver(BaseModel):
    id: int
    full_name: str
    tab_number: str = ""
    licence_series: str = ""
    licence_number: str = ""
    licence_issued_at: str | None = None
    licence_class: str = ""
    snils: str = ""
    licence_card: str = ""
    is_active: bool = True
    created_at: str


class DriverBrief(BaseModel):
    """Водитель для СПИСКА — без персональных данных.

    СНИЛС и номер водительского удостоверения — персональные данные. Правило
    проекта: в списковых ответах их не отдавать (CLAUDE.md §4.4). Причина не
    формальная: справочник открыт всем вошедшим, а вошедших тридцать человек,
    и общий список с СНИЛС коллег — это выгрузка персональных данных по
    одному запросу, без всякого взлома.

    Заполнено поле или нет, знать всё же надо: пустой СНИЛС делает путевой лист
    недействительным, и увидеть это должно быть можно, не показывая сам номер.
    Отсюда два признака вместо двух значений.

    Сами номера отдаёт `GET /drivers/{id}` — по одному, когда человек
    осознанно открыл карточку, и печать листа (`print_data`), где они
    обязательны по форме.
    """

    id: int
    full_name: str
    tab_number: str = ""
    licence_series: str = ""
    licence_issued_at: str | None = None
    licence_class: str = ""
    licence_card: str = ""
    is_active: bool = True
    created_at: str
    есть_снилс: bool = False
    есть_удостоверение: bool = False


class DriverCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=120)
    tab_number: str = Field(default="", max_length=20)
    licence_series: str = Field(default="", max_length=10)
    licence_number: str = Field(default="", max_length=20)
    licence_issued_at: str | None = None
    licence_class: str = Field(default="", max_length=20)
    snils: str = Field(default="", max_length=20)
    licence_card: str = Field(default="", max_length=20)

    @field_validator("full_name")
    @classmethod
    def _name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("ФИО водителя не может быть пустым")
        return v

    @field_validator("licence_card")
    @classmethod
    def _known_card(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in LICENCE_CARDS:
            raise ValueError("Лицензионная карточка: «стандартная» или «ограниченная»")
        return v


class DriverUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=120)
    tab_number: str | None = Field(default=None, max_length=20)
    licence_series: str | None = Field(default=None, max_length=10)
    licence_number: str | None = Field(default=None, max_length=20)
    licence_issued_at: str | None = None
    licence_class: str | None = Field(default=None, max_length=20)
    snils: str | None = Field(default=None, max_length=20)
    licence_card: str | None = Field(default=None, max_length=20)
    is_active: bool | None = None


class Trailer(BaseModel):
    id: int
    mark: str = ""
    reg_number: str
    series: str = ""
    code: str = ""
    is_active: bool = True
    created_at: str


class TrailerCreate(BaseModel):
    mark: str = Field(default="", max_length=60)
    reg_number: str = Field(min_length=1, max_length=20)
    series: str = Field(default="", max_length=10)
    code: str = Field(default="", max_length=20)

    @field_validator("reg_number")
    @classmethod
    def _reg_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Регистрационный номер прицепа не может быть пустым")
        return v


class Downtime(BaseModel):
    """Простой на линии — оборотная сторона формы 4-С."""

    id: int | None = None
    name: str = ""
    reason: str = ""
    started_at: str | None = None
    ended_at: str | None = None
    responsible_name: str = ""


class WaybillFields(BaseModel):
    """Все заполняемые поля бланка.

    Единый набор для чтения, создания и правки: полей под сотню, и три
    независимых списка разошлись бы при первом же добавлении реквизита.
    Частичное обновление работает через exclude_unset — неприсланное поле не
    попадает в UPDATE, поэтому «пустая строка» и «не трогать» не путаются.
    """

    form: str = "3"
    org_id: int | None = None
    series: str = ""
    number: str = ""
    date_from: str = ""
    date_to: str = ""
    vehicle_id: int | None = None
    driver_id: int | None = None
    column_number: str = ""
    brigade: str = ""
    work_mode: str = ""
    escorts: str = ""

    # Задание водителю
    communication_type: str = ""
    transport_type: str = ""
    customer_name: str = ""
    customer_address: str = ""
    pickup_address: str = ""
    delivery_address: str = ""
    cargo_name: str = ""
    planned_arrival_at: str | None = None
    planned_trips: int | None = Field(default=None, ge=0, le=1000)
    planned_distance_km: float | None = Field(default=None, ge=0, le=100_000)
    planned_cargo_kg: int | None = Field(default=None, ge=0, le=1_000_000)
    fuel_to_issue_l: float | None = Field(default=None, ge=0, le=5000)

    # Работа водителя и автомобиля: график против факта
    scheduled_departure_at: str | None = None
    scheduled_return_at: str | None = None
    departure_at: str | None = None
    return_at: str | None = None
    odometer_start_km: float | None = Field(default=None, ge=0, le=10_000_000)
    odometer_end_km: float | None = Field(default=None, ge=0, le=10_000_000)
    zero_run_start_km: float | None = Field(default=None, ge=0, le=100_000)
    zero_run_end_km: float | None = Field(default=None, ge=0, le=100_000)

    # Движение горючего
    fuel_brand: str = ""
    fuel_code: str = ""
    fuel_sheet_number: str = ""
    fuel_start_l: float | None = Field(default=None, ge=0, le=5000)
    fuel_issued_l: float | None = Field(default=None, ge=0, le=5000)
    fuel_end_l: float | None = Field(default=None, ge=0, le=5000)
    fuel_returned_l: float | None = Field(default=None, ge=0, le=5000)
    fuel_norm_l_100km: float | None = Field(default=None, ge=0, le=200)
    fuel_coeff_equipment: float | None = Field(default=None, ge=0, le=10)
    fuel_coeff_engine: float | None = Field(default=None, ge=0, le=10)
    equipment_time_min: int | None = Field(default=None, ge=0, le=100_000)
    engine_time_min: int | None = Field(default=None, ge=0, le=100_000)

    # Медосмотр, техконтроль, приём-передача
    medical_pre_mark: str = ""
    medical_pre_at: str | None = None
    medical_pre_position: str = ""
    medical_pre_name: str = ""
    medical_post_mark: str = ""
    medical_post_at: str | None = None
    medical_post_position: str = ""
    medical_post_name: str = ""
    tech_control_mark: str = ""
    tech_control_at: str | None = None
    tech_control_name: str = ""
    licence_checked: bool = False
    mechanic_name: str = ""
    dispatcher_name: str = ""
    accepted_by_driver: str = ""
    return_condition: str = ""
    vehicle_handed_by: str = ""
    vehicle_taken_by: str = ""

    # Результаты работы за смену
    total_time_min: int | None = Field(default=None, ge=0, le=100_000)
    driving_time_min: int | None = Field(default=None, ge=0, le=100_000)
    idle_vehicle_min: int | None = Field(default=None, ge=0, le=100_000)
    idle_loading_min: int | None = Field(default=None, ge=0, le=100_000)
    idle_repair_min: int | None = Field(default=None, ge=0, le=100_000)
    idle_extra_min: int | None = Field(default=None, ge=0, le=100_000)
    trips_done: int | None = Field(default=None, ge=0, le=1000)
    stops_done: int | None = Field(default=None, ge=0, le=1000)
    run_total_km: float | None = Field(default=None, ge=0, le=100_000)
    run_loaded_km: float | None = Field(default=None, ge=0, le=100_000)
    run_trailer_km: float | None = Field(default=None, ge=0, le=100_000)
    cargo_done_kg: int | None = Field(default=None, ge=0, le=1_000_000)
    cargo_tkm: float | None = Field(default=None, ge=0, le=1_000_000)
    days_in_work: int | None = Field(default=None, ge=0, le=366)
    fuel_used_norm_l: float | None = Field(default=None, ge=0, le=5000)
    fuel_used_fact_l: float | None = Field(default=None, ge=0, le=5000)

    # Товарно-транспортные документы и таксировка
    ttd_count: int | None = Field(default=None, ge=0, le=1000)
    ttd_handed_by: str = ""
    ttd_taken_by: str = ""
    taxation_text: str = ""
    taxer_name: str = ""

    # Зарплата
    salary_km_rub: float | None = Field(default=None, ge=0, le=10_000_000)
    salary_hours_rub: float | None = Field(default=None, ge=0, le=10_000_000)
    salary_vehicle_rub: float | None = Field(default=None, ge=0, le=10_000_000)
    salary_trailer_rub: float | None = Field(default=None, ge=0, le=10_000_000)
    salary_total_rub: float | None = Field(default=None, ge=0, le=10_000_000)
    calc_position: str = ""
    calc_name: str = ""

    special_marks: str = ""
    owner_marks: str = ""
    delay_notes: str = ""

    status: str = "draft"

    @field_validator("form")
    @classmethod
    def _known_form(cls, v: str) -> str:
        if v not in WAYBILL_FORMS:
            raise ValueError("Форма листа: «3» (легковой) или «4-S» (грузовой)")
        return v

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str) -> str:
        if v not in WAYBILL_STATUSES:
            raise ValueError("Состояние листа: draft, issued или closed")
        return v


class WaybillCreate(WaybillFields):
    """Новый лист: машина и срок обязательны, остальное дозаполняется."""

    vehicle_id: int
    date_from: str = Field(min_length=1)
    date_to: str = Field(min_length=1)


class WaybillUpdate(WaybillFields):
    """Частичная правка. Присылать только изменённые поля."""


class Waybill(WaybillFields):
    id: int
    # Подставляется из справочников для показа и печати
    org_name: str = ""
    vehicle_name: str = ""
    vehicle_plate: str = ""
    vehicle_mark: str = ""
    garage_number: str = ""
    driver_name: str = ""
    driver_tab_number: str = ""
    # Расход, посчитанный по цепочке бланка: остаток при выезде + выдано −
    # остаток при возвращении − сдано. None, если данных не хватает.
    fuel_balance_l: float | None = None
    # Экономия (> 0) или перерасход (< 0) против нормы. None без нормы.
    fuel_saving_l: float | None = None
    # Расход по норме: пробег × норма. Считается, когда норма утверждена и
    # пробег известен; иначе None и в бланке прочерк. Отдаётся отдельно от
    # fuel_used_norm_l: то — цифра, вписанная человеком, и она главнее.
    fuel_by_norm_l: float | None = None
    # Графы, заполненные программой из карточки машины при выписке листа.
    # Интерфейс помечает их и подписывает источник: значение, взявшееся само,
    # без объяснения читается как чужая ошибка, и его либо боятся трогать,
    # либо молча перебивают.
    autofilled: list[str] = Field(default_factory=list)
    trailers: list[Trailer] = Field(default_factory=list)
    downtimes: list[Downtime] = Field(default_factory=list)
    created_by: str = ""
    created_at: str = ""
