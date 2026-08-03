"""Вход кириллическим логином.

Регрессия на отказ в бою: логин «Разраб» ронял вход с HTTP 500. Заголовки
HTTP допускают только latin-1, а запомненный логин кладётся в cookie — при
кириллице starlette падал с UnicodeEncodeError. Для русской компании это отказ
на самом обычном сценарии: логины здесь кириллицей и будут.
"""

from __future__ import annotations

import pytest
from fastapi import Response
from fire_safety_backend.views import auth


@pytest.mark.parametrize("login", ["Разраб", "Иванов", "Пётр Ёлкин", "ivanov"])
def test_remembered_login_survives_the_round_trip(login: str) -> None:
    """Логин обязан вернуться из cookie ровно таким, каким его записали."""
    response = Response()
    auth._remember_login(response, login)

    raw = next(v.decode("latin-1") for k, v in response.raw_headers if k == b"set-cookie")
    stored = raw.split("=", 1)[1].split(";", 1)[0]
    from urllib.parse import unquote

    assert unquote(stored) == login


def test_cookie_value_is_ascii_only() -> None:
    """Собственно причина падения: значение cookie обязано кодироваться в
    latin-1, иначе starlette бросает UnicodeEncodeError и вход отдаёт 500."""
    response = Response()
    auth._remember_login(response, "Разраб")
    for key, value in response.raw_headers:
        if key == b"set-cookie":
            value.decode("latin-1")  # не должно бросить
            assert all(c < 128 for c in value)
