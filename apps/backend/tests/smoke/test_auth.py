"""Вход по логину и защита роутеров.

Пароля в приложении нет: сотрудник вводит логин один раз, дальше устройство
подставляет его само, и остаётся нажать кнопку. Честная оговорка о том, чего
это стоит, — в docstring services/auth.py; здесь проверяется, что из защиты
реально осталось и что оно работает.

Осталось три вещи, и все три тут проверяются:

1. Без входа не открывается ничего, кроме формы входа и `/api/health`.
2. Войти можно только под СУЩЕСТВУЮЩИМ логином — придумать на ходу нельзя,
   иначе опечатка создавала бы нового «сотрудника» и он терял бы свою историю.
3. Отключённая учётная запись не пускает и теряет свои открытые сессии.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fire_safety_backend.services import auth
from fire_safety_backend.views.auth import LAST_LOGIN_COOKIE

# --- Вход ---


def test_login_with_existing_login(anon_client: TestClient, test_login: str) -> None:
    r = anon_client.post("/api/auth/login", json={"login": test_login})
    assert r.status_code == 200
    assert r.json()["login"] == test_login


def test_unknown_login_is_refused(anon_client: TestClient) -> None:
    """Придумать логин на ходу нельзя: иначе опечатка заводила бы нового
    «сотрудника», и человек терял бы доступ к своим документам."""
    r = anon_client.post("/api/auth/login", json={"login": "нет-такого"})
    assert r.status_code == 401
    assert auth.authenticate("нет-такого") is None


def test_empty_login_is_refused(anon_client: TestClient) -> None:
    assert anon_client.post("/api/auth/login", json={"login": ""}).status_code == 422
    assert auth.authenticate("   ") is None


def test_password_field_is_not_required(anon_client: TestClient, test_login: str) -> None:
    """Ровно то, ради чего переделывали: тело запроса — один логин."""
    assert anon_client.post("/api/auth/login", json={"login": test_login}).status_code == 200


def test_disabled_account_cannot_log_in(client: TestClient, test_login: str) -> None:
    """Отключение — единственный способ закрыть доступ уволившемуся, раз
    пароля нет."""
    assert auth.set_disabled(test_login, True) is True
    assert auth.authenticate(test_login) is None


def test_disabling_closes_open_sessions(client: TestClient, test_login: str) -> None:
    """Иначе отключённый сотрудник продолжает работать по открытой вкладке."""
    assert client.get("/api/history").status_code == 200
    auth.set_disabled(test_login, True)
    assert client.get("/api/history").status_code == 401


def test_disabled_account_can_be_brought_back(client: TestClient, test_login: str) -> None:
    auth.set_disabled(test_login, True)
    auth.set_disabled(test_login, False)
    assert auth.authenticate(test_login) is not None


# --- Автоподстановка логина ---


def test_login_is_remembered_on_the_device(anon_client: TestClient, test_login: str) -> None:
    """Главное в этой переделке: со второго раза логин вводить не надо."""
    r = anon_client.post("/api/auth/login", json={"login": test_login})
    assert r.cookies.get(LAST_LOGIN_COOKIE) == test_login

    remembered = anon_client.get("/api/auth/me").json()["remembered"]
    assert remembered == test_login


def test_remembered_login_is_readable_by_the_page(anon_client: TestClient, test_login: str) -> None:
    """Cookie с логином намеренно НЕ httponly — её читает страница входа.
    Секрета в ней нет: доступ даёт сессия, а не логин."""
    r = anon_client.post("/api/auth/login", json={"login": test_login})
    cookie = "".join(
        h for h in r.headers.get_list("set-cookie") if h.startswith(LAST_LOGIN_COOKIE)
    ).lower()
    assert "httponly" not in cookie


def test_session_cookie_is_still_protected(anon_client: TestClient, test_login: str) -> None:
    """А вот сессионная cookie обязана остаться закрытой от JS."""
    r = anon_client.post("/api/auth/login", json={"login": test_login})
    session_cookie = "".join(
        h for h in r.headers.get_list("set-cookie") if h.startswith("assistent_pb_session")
    ).lower()
    assert "httponly" in session_cookie
    assert "samesite=lax" in session_cookie


def test_logout_keeps_the_remembered_login(client: TestClient, test_login: str) -> None:
    """Выход — это «закончил работу», а не «это не мой компьютер». Иначе
    следующий вход снова требовал бы набирать логин, ради чего всё и делалось."""
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["remembered"] == test_login


def test_forget_device_clears_the_login(client: TestClient) -> None:
    """На общем компьютере подставленный логин коллеги — приглашение войти
    под ним."""
    assert client.post("/api/auth/forget-device").status_code == 200
    body = client.get("/api/auth/me").json()
    assert body["remembered"] == ""
    assert body["login"] is None


def test_nothing_is_remembered_before_the_first_login(anon_client: TestClient) -> None:
    anon_client.cookies.clear()
    assert anon_client.get("/api/auth/me").json()["remembered"] == ""


# --- Что закрыто, а что открыто ---


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/tasks"),
        ("get", "/api/history"),
        ("get", "/api/addressees"),
        ("get", "/api/download/письмо.docx"),
        ("get", "/api/data/status"),
        ("post", "/api/data/purge"),
        ("post", "/api/spellcheck"),
        ("post", "/api/letter/render"),
    ],
)
def test_api_is_closed_without_login(anon_client: TestClient, method: str, path: str) -> None:
    """Зависимость навешена на роутеры целиком — забыть её на новой ручке
    нельзя, и этот параметризованный список тому свидетель."""
    assert getattr(anon_client, method)(path).status_code == 401


def test_health_is_open_but_hides_security_details(anon_client: TestClient) -> None:
    """«Сервер жив» нужно видеть до входа и из мониторинга. А блок
    безопасности — карта слабых мест, постороннему её знать незачем."""
    r = anon_client.get("/api/health")
    assert r.status_code == 200
    assert "ollama" in r.json()
    assert "security" not in r.json()


def test_health_shows_security_after_login(client: TestClient) -> None:
    assert "security" in client.get("/api/health").json()


def test_pages_redirect_to_login(anon_client: TestClient) -> None:
    """Голый 401 в браузере выглядит как поломка сервера, а не «представьтесь»."""
    for path in ("/", "/legal.html", "/history.html"):
        r = anon_client.get(path, follow_redirects=False)
        assert r.status_code == 303, path
        assert r.headers["location"] == "/login.html"


def test_login_page_is_open(anon_client: TestClient) -> None:
    r = anon_client.get("/login.html")
    assert r.status_code == 200
    assert "Вход" in r.text


def test_logged_in_user_is_bounced_off_the_login_page(client: TestClient) -> None:
    r = client.get("/login.html", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"


# --- Сессии ---


def test_logout_revokes_the_session(client: TestClient) -> None:
    """Сессии лежат в БД именно ради этого: подписанный токен отозвать нечем."""
    assert client.get("/api/history").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/history").status_code == 401


def test_stale_session_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Забытая открытой вкладка на чужом компьютере не должна оставаться
    входом навсегда."""
    monkeypatch.setattr(auth, "SESSION_IDLE_HOURS", -1)
    assert client.get("/api/history").status_code == 401


def test_me_reports_whether_any_accounts_exist(anon_client: TestClient) -> None:
    """Свежий сервер без учётных записей обязан сказать это прямо, иначе любой
    ввод отвечает «такой учётной записи нет» и человек ищет ошибку в логине."""
    body = anon_client.get("/api/auth/me").json()
    assert body["login"] is None
    assert body["any_users"] is True

    from fire_safety_backend.infrastructure.db import connect

    with connect() as conn:
        conn.execute("DELETE FROM users")
    assert anon_client.get("/api/auth/me").json()["any_users"] is False
