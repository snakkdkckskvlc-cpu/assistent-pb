"""Юнит-тесты классификатора типа документа (services/classify.py)."""

from __future__ import annotations

from fire_safety_backend.services.classify import classify_document

_CONTRACT = """
ДОГОВОР ПОДРЯДА №14/2026
г. Липецк

ООО «ПожСервис», именуемое в дальнейшем «Подрядчик», и ПАО «НЛМК»,
именуемое в дальнейшем «Заказчик», заключили настоящий договор.

1. ПРЕДМЕТ ДОГОВОРА
1.1. Подрядчик обязуется выполнить работы по техническому обслуживанию.

2. ОБЯЗАННОСТИ СТОРОН
2.1. Заказчик обязуется оплатить работы в срок.
"""

_LETTER = """
Уважаемый Сергей Сергеевич!

ООО «ПожСервис» доводим до Вашего сведения, что плановое техническое
обслуживание систем пожарной сигнализации назначено на август.

Просим Вас согласовать даты допуска бригады.

С уважением,
Директор О.Н. Сляднев
"""

_ACT = """
АКТ №7 выполненных работ

Комиссия в составе представителей Заказчика и Подрядчика составила
настоящий акт о том, что работы по монтажу выполнены в полном объёме.

Сдал: ______  Принял: ______
"""

_ESTIMATE = """
Локальная смета №02-01
Наименование работ и затрат | Ед. изм | Кол-во | Стоимость
Монтаж извещателя ИП-212    | шт      | 40     | 12 000
Итого по смете: 480 000 руб.
"""


def test_contract_detected() -> None:
    result = classify_document(_CONTRACT)
    assert result["type"] == "договор"
    assert result["confidence"] > 0.4
    assert "предмет договора" in result["signals"]


def test_letter_detected() -> None:
    result = classify_document(_LETTER)
    assert result["type"] == "письмо"
    assert "уважаем" in result["signals"]


def test_act_detected() -> None:
    assert classify_document(_ACT)["type"] == "акт"


def test_estimate_detected() -> None:
    assert classify_document(_ESTIMATE)["type"] == "смета"


def test_unrelated_text_is_other() -> None:
    result = classify_document("Погода сегодня солнечная, ветер северо-западный.")
    assert result["type"] == "прочее"
    assert result["confidence"] == 0.0
    assert result["signals"] == []


def test_empty_text_is_other() -> None:
    assert classify_document("")["type"] == "прочее"


def test_type_read_from_document_head_only() -> None:
    # Тип определяется по шапке/первым абзацам: маркеры договора глубже
    # 4000 символов не должны перекрасить письмо в договор.
    text = _LETTER + ("х" * 4000) + _CONTRACT
    assert classify_document(text)["type"] == "письмо"
