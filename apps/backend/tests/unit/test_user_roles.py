"""Роль сотрудника: чем человек занят, с того и начинается его экран.

Роль — НЕ право доступа. Она ничего не открывает и не закрывает, только ставит
нужную функцию первой на «Сегодня». Тесты держат именно это свойство: секретарь
не должен получить от роли ни больше, ни меньше доступа.
"""

from __future__ import annotations

import pytest
from fire_safety_backend.services import auth


def test_new_user_has_no_role() -> None:
    """Пустая роль — обычный экран. Так живут все, кто заведён до ролей."""
    auth.create_user("роль-пусто")
    assert auth.authenticate("роль-пусто").role == ""


def test_role_is_saved_on_creation() -> None:
    auth.create_user("роль-секретарь", role="секретарь")
    assert auth.authenticate("роль-секретарь").role == "секретарь"


def test_role_changes_without_recreating_the_account() -> None:
    """Должность меняется, а история задач у человека остаётся его."""
    auth.create_user("роль-смена", role="инженер")
    assert auth.set_role("роль-смена", "руководитель") is True
    assert auth.authenticate("роль-смена").role == "руководитель"


def test_role_can_be_removed() -> None:
    auth.create_user("роль-снять", role="бухгалтер")
    auth.set_role("роль-снять", "")
    assert auth.authenticate("роль-снять").role == ""


def test_unknown_role_is_rejected() -> None:
    """Опечатка «инжнер» тихо вернула бы обычный экран, и разбираться, почему у
    человека «не тот» интерфейс, пришлось бы долго. Лучше отказ сразу."""
    auth.create_user("роль-опечатка")
    with pytest.raises(ValueError, match="Неизвестная роль"):
        auth.set_role("роль-опечатка", "инжнер")
    assert auth.authenticate("роль-опечатка").role == ""


def test_role_does_not_grant_admin() -> None:
    """Главное свойство: роль — подсказка интерфейсу, а не право.

    Разбивка по сотрудникам на экране «Что происходит» открыта только
    администратору, и «руководитель» не должен её получить ролью.
    """
    auth.create_user("роль-руководитель", role="руководитель")
    user = auth.authenticate("роль-руководитель")
    assert user.role == "руководитель"
    assert user.is_admin is False


def test_missing_user_is_reported_not_crashed() -> None:
    assert auth.set_role("такого-нет", "инженер") is False
