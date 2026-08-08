"""Список водителей не отдаёт персональные данные.

Правило проекта, записанное дважды — в `CLAUDE.md` §4.4 и в
`.claude/rules/domain.md`: справочник водителей содержит СНИЛС и номер
водительского удостоверения, и **в списковых ответах эти поля не отдаются**.

Правило было, а проверки не было — и список отдавал их всем. Причина не в
злом умысле, а в `SELECT *` и модели ответа `Driver`: столбцы приезжали в
ответ сами, ровно в тот день, когда их добавили в таблицу.

Почему это существеннее, чем кажется. Справочник открыт КАЖДОМУ вошедшему, а
вошедших в компании около тридцати. Значит один запрос отдавал СНИЛС всех
водителей любому сотруднику — это выгрузка персональных данных, для которой не
нужно ничего взламывать. Роутер под входом от этого не спасает: вход есть у
всех.

Что взамен. Список отдаёт признаки «заполнено / пусто»: пустой СНИЛС делает
путевой лист недействительным, и видеть это надо, не показывая номер. Сами
номера отдаёт `GET /drivers/{id}` — по одному, когда карточку открыли, чтобы
поправить, — и печать листа, где они обязательны по форме.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from fire_safety_backend.models.waybill import DriverCreate
from fire_safety_backend.services import waybills

СНИЛС = "112-233-445 95"
НОМЕР_ВУ = "445566"

# Поля, которых в списке быть не должно ни под каким видом.
ПЕРСОНАЛЬНЫЕ = ("snils", "licence_number")


def _завести(client: TestClient) -> int:
    driver = waybills.create_driver(
        DriverCreate(
            full_name="Сидоров Иван Петрович",
            snils=СНИЛС,
            licence_series="4823",
            licence_number=НОМЕР_ВУ,
        )
    )
    return driver.id


def test_list_does_not_leak_personal_data(client: TestClient) -> None:
    """Главный тест файла. Проверяется и ключ, и само значение: убрать поле из
    модели, но оставить номер в другом ключе — та же утечка."""
    _завести(client)
    r = client.get("/api/transport/drivers")
    assert r.status_code == 200
    сырой = r.text
    for поле in ПЕРСОНАЛЬНЫЕ:
        assert поле not in сырой, f"поле {поле} попало в общий список водителей"
    assert СНИЛС not in сырой, "СНИЛС попал в общий список водителей"
    assert НОМЕР_ВУ not in сырой, "номер удостоверения попал в общий список"


def test_list_still_says_whether_the_fields_are_filled(client: TestClient) -> None:
    """Скрыть номер — не значит скрыть факт его отсутствия. Без СНИЛС путевой
    лист недействителен, и заметить это должно быть можно."""
    _завести(client)
    waybills.create_driver(DriverCreate(full_name="Без документов"))
    список = client.get("/api/transport/drivers").json()
    по_имени = {d["full_name"]: d for d in список}
    assert по_имени["Сидоров Иван Петрович"]["есть_снилс"] is True
    assert по_имени["Сидоров Иван Петрович"]["есть_удостоверение"] is True
    assert по_имени["Без документов"]["есть_снилс"] is False
    assert по_имени["Без документов"]["есть_удостоверение"] is False


def test_single_card_does_return_the_numbers(client: TestClient) -> None:
    """Обратная сторона: если карточку нельзя открыть, поправить опечатку в
    СНИЛС будет нечем, и правило превратится в запрет работать."""
    driver_id = _завести(client)
    карточка = client.get(f"/api/transport/drivers/{driver_id}").json()
    assert карточка["snils"] == СНИЛС
    assert карточка["licence_number"] == НОМЕР_ВУ


def test_missing_driver_card_is_404(client: TestClient) -> None:
    assert client.get("/api/transport/drivers/999999").status_code == 404


def test_printing_still_gets_the_numbers(client: TestClient) -> None:
    """Печать листа — второе место, где номера обязаны быть: без них бланк
    недействителен. Сужение списка не должно задеть печать."""
    driver_id = _завести(client)
    водитель = waybills.get_driver(driver_id)
    assert водитель.snils == СНИЛС
    assert водитель.licence_number == НОМЕР_ВУ
