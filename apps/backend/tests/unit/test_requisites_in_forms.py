"""Реквизиты проверяются там, где их вводят, а не только в чистой функции.

Модуль `services/requisites.py` сам по себе ничего не защищает: пока он никем
не вызван, водитель с испорченным СНИЛС заводится так же спокойно, как раньше.
Эти тесты держат подключение — что проверка стоит на пути записи в справочник.

Почему именно здесь. СНИЛС и ОКПО печатаются в путевом листе, это его
обязательные реквизиты. Опечатку в них замечают при сдаче документов, когда
лист уже подписан; на вводе она ловится контрольной суммой за миллисекунду.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fire_safety_backend.models.waybill import DriverCreate, DriverUpdate, OrganizationCreate
from fire_safety_backend.services import waybills

СНИЛС_ВЕРНЫЙ = "112-233-445 95"
СНИЛС_БИТЫЙ = "112-233-445 96"
ОГРН_ВЕРНЫЙ = "1054800315184"


def test_driver_with_broken_snils_is_not_created(client: TestClient) -> None:
    with pytest.raises(ValueError, match="СНИЛС"):
        waybills.create_driver(DriverCreate(full_name="Петров А. С.", snils=СНИЛС_БИТЫЙ))


def test_driver_with_valid_snils_is_created(client: TestClient) -> None:
    driver = waybills.create_driver(DriverCreate(full_name="Петров А. С.", snils=СНИЛС_ВЕРНЫЙ))
    assert driver.id > 0


def test_driver_without_snils_is_created(client: TestClient) -> None:
    """Обязательность решает форма, а не проверка: карточку заполняют по частям,
    и требовать СНИЛС в момент заведения значило бы не дать завести водителя,
    пока не найдут его документы."""
    assert waybills.create_driver(DriverCreate(full_name="Без документов")).id > 0


def test_editing_the_name_does_not_require_snils(client: TestClient) -> None:
    """DriverUpdate частичный. Проверять непереданное поле — значит падать на
    правке одной фамилии."""
    driver = waybills.create_driver(DriverCreate(full_name="Сидоров", snils=СНИЛС_ВЕРНЫЙ))
    обновлён = waybills.update_driver(driver.id, DriverUpdate(full_name="Сидоров И. И."))
    assert обновлён.full_name == "Сидоров И. И."


def test_broken_snils_cannot_be_slipped_in_by_editing(client: TestClient) -> None:
    """Дыра, которую легко не заметить: проверить только создание и оставить
    правку открытой."""
    driver = waybills.create_driver(DriverCreate(full_name="Кузнецов", snils=СНИЛС_ВЕРНЫЙ))
    with pytest.raises(ValueError, match="СНИЛС"):
        waybills.update_driver(driver.id, DriverUpdate(snils=СНИЛС_БИТЫЙ))
    # Записанное осталось прежним, а не «почти прежним»: СНИЛС хранится ровно
    # так, как его ввели, — проверено на живой записи.
    assert waybills.get_driver(driver.id).snils == СНИЛС_ВЕРНЫЙ


def test_organization_with_broken_ogrn_is_rejected(client: TestClient) -> None:
    with pytest.raises(ValueError, match="ОГРН"):
        waybills.create_organization(OrganizationCreate(name="ООО «Тест»", ogrn="1054800315185"))


def test_organization_with_real_ogrn_is_created(client: TestClient) -> None:
    org = waybills.create_organization(OrganizationCreate(name="ООО «Тест»", ogrn=ОГРН_ВЕРНЫЙ))
    assert org.id > 0


def test_seeded_organizations_pass_their_own_check(client: TestClient) -> None:
    """Сиды заливаются в обход create_organization, поэтому проверка их не
    видит. Если бы в них лежал испорченный ОГРН, приложение молча печатало бы
    его в каждом путевом листе — а тест бы этого не заметил.
    """
    for org in waybills.list_organizations():
        payload = OrganizationCreate(
            name=org.name, address=org.address, phone=org.phone, okpo=org.okpo, ogrn=org.ogrn
        )
        waybills._проверить_реквизиты_организации(payload)  # не должно бросить
