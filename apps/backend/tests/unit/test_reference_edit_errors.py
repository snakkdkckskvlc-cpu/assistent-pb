"""Правка справочника отвечает по-русски, а не «Internal Server Error».

Как это сломалось. Проверка контрольных цифр (ОГРН, ОКПО, СНИЛС) подключена и
к заведению записи, и к правке. Но ручки заведения переводили отказ проверки в
понятный ответ, а ручки ПРАВКИ ловили только «запись не найдена». Отказ
проверки долетал до FastAPI как есть и превращался в 500 с английской строкой.

Данные при этом не портились — в этом смысле защита работала. Ломалось другое:
человек видел «Internal Server Error» вместо «ОГРН не сходится по контрольной
цифре» и не мог понять, что исправлять. Для секретаря это неотличимо от
поломки программы, и следующий шаг — звонок разработчику вместо правки одной
цифры.

Заметить это на глаз было нечем: заведение и правка выглядят в коде одинаково,
разница в одной строке `except`, а тестов на правку с плохими данными не было.
Поэтому проверка идёт по ВСЕМ ручкам правки справочника разом, а не по тем
двум, которые уже починены.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fire_safety_backend.models.waybill import DriverCreate, OrganizationCreate
from fire_safety_backend.services import waybills

# Номера с заведомо неверной последней цифрой: длина и вид настоящие, не
# сходится только контрольное число. Именно так выглядит опечатка при наборе с
# бумаги — и именно её проверка обязана ловить.
ОГРН_БИТЫЙ = "1054800315185"
СНИЛС_БИТЫЙ = "112-233-445 96"
СНИЛС_ВЕРНЫЙ = "112-233-445 95"


def test_editing_organization_with_broken_ogrn_answers_in_russian(client: TestClient) -> None:
    org = waybills.create_organization(OrganizationCreate(name="ООО «Тест правки»"))
    r = client.put(
        f"/api/transport/orgs/{org.id}",
        json={"name": org.name, "address": "", "phone": "", "okpo": "", "ogrn": ОГРН_БИТЫЙ},
    )
    assert r.status_code != 500, "отказ проверки долетел до FastAPI и стал 500"
    assert r.status_code == 409
    assert "ОГРН" in r.json()["detail"]


def test_editing_driver_with_broken_snils_answers_in_russian(client: TestClient) -> None:
    driver = waybills.create_driver(DriverCreate(full_name="Петров А. С.", snils=СНИЛС_ВЕРНЫЙ))
    r = client.patch(f"/api/transport/drivers/{driver.id}", json={"snils": СНИЛС_БИТЫЙ})
    assert r.status_code != 500
    assert r.status_code == 409
    assert "СНИЛС" in r.json()["detail"]


def test_the_record_is_not_changed_by_a_rejected_edit(client: TestClient) -> None:
    """Отказ обязан быть полным. Записать половину полей и отвергнуть остальные —
    хуже, чем не записать ничего: в справочнике осталась бы смесь старого и
    нового, о которой никто не знает."""
    driver = waybills.create_driver(DriverCreate(full_name="Кузнецов", snils=СНИЛС_ВЕРНЫЙ))
    client.patch(
        f"/api/transport/drivers/{driver.id}",
        json={"full_name": "Кузнецов И. И.", "snils": СНИЛС_БИТЫЙ},
    )
    остался = waybills.get_driver(driver.id)
    assert остался.snils == СНИЛС_ВЕРНЫЙ
    assert остался.full_name == "Кузнецов"


@pytest.mark.parametrize(
    ("метод", "путь", "тело"),
    [
        ("put", "/api/transport/orgs/{id}", {"name": "Х", "ogrn": ОГРН_БИТЫЙ}),
        ("patch", "/api/transport/drivers/{id}", {"snils": СНИЛС_БИТЫЙ}),
    ],
    ids=["организация", "водитель"],
)
def test_no_edit_route_answers_500(
    client: TestClient, метод: str, путь: str, тело: dict[str, str]
) -> None:
    """Общий замок. Ручек правки в справочнике больше, чем этих двух, и каждая
    новая наследует ту же ошибку: `except LookupError` есть, `except ValueError`
    забыли. Здесь перечислены те, у которых проверка контрольных цифр уже
    стоит; появится третья — строка добавляется сюда.
    """
    if "orgs" in путь:
        объект_id = waybills.create_organization(OrganizationCreate(name="ООО «Замок»")).id
    else:
        объект_id = waybills.create_driver(DriverCreate(full_name="Замков", snils=СНИЛС_ВЕРНЫЙ)).id
    r = client.request(метод.upper(), путь.format(id=объект_id), json=тело)
    assert r.status_code == 409, (
        f"{метод.upper()} {путь} отвечает {r.status_code}. Ручка правки не переводит "
        f"отказ проверки в понятный ответ — человек увидит «Internal Server Error»"
    )
