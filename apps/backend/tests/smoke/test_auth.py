"""Вход по паролю и защита роутеров.

Приложение переезжает на сервер и слушает всю внутреннюю сеть. До этого
разграничения не было вовсе — и это было безопасно ровно пока backend слушал
127.0.0.1. Теперь единственная преграда между сетью и договорами компании —
эта авторизация, поэтому проверяется не «форма входа работает», а что
закрытое действительно закрыто.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fire_safety_backend.services import auth


@pytest.fixture(autouse=True)
def _clean_attempts():
    auth.reset_failed_attempts()
    yield
    auth.reset_failed_attempts()


# --- Хеширование ---


def test_password_is_not_stored_as_is(
    client: TestClient, test_login: str, test_password: str
) -> None:
    """Главное свойство: утёкшая база не отдаёт пароли."""
    from fire_safety_backend.infrastructure.db import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT password_hash, salt FROM users WHERE login = ?", (test_login,)
        ).fetchone()
    assert test_password.encode() not in bytes(row["password_hash"])
    assert test_password not in str(row["password_hash"])
    assert len(bytes(row["salt"])) >= 16


def test_same_password_gives_different_hashes() -> None:
    """Своя соль у каждого: одинаковые пароли разных людей не совпадают в базе,
    и радужная таблица бесполезна."""
    first, salt1 = auth.hash_password("одинаковый-пароль")
    second, salt2 = auth.hash_password("одинаковый-пароль")
    assert salt1 != salt2
    assert first != second


def test_verify_accepts_right_and_rejects_wrong() -> None:
    digest, salt = auth.hash_password("правильный-пароль")
    assert auth.verify_password("правильный-пароль", digest, salt) is True
    assert auth.verify_password("правильный-паролЬ", digest, salt) is False


def test_short_password_is_refused() -> None:
    with pytest.raises(ValueError):
        auth.create_user("коротышка", "1234567")


# --- Вход ---


def test_login_with_right_password(
    anon_client: TestClient, test_login: str, test_password: str
) -> None:
    r = anon_client.post("/api/auth/login", json={"login": test_login, "password": test_password})
    assert r.status_code == 200
    assert r.json()["login"] == test_login


def test_login_with_wrong_password_fails(anon_client: TestClient, test_login: str) -> None:
    r = anon_client.post("/api/auth/login", json={"login": test_login, "password": "не тот"})
    assert r.status_code == 401


def test_unknown_login_and_wrong_password_look_the_same(
    anon_client: TestClient, test_login: str
) -> None:
    """Разные сообщения подсказали бы перебирающему, какие логины существуют."""
    wrong_pass = anon_client.post(
        "/api/auth/login", json={"login": test_login, "password": "не тот"}
    )
    no_such = anon_client.post(
        "/api/auth/login", json={"login": "нет-такого", "password": "не тот"}
    )
    assert wrong_pass.status_code == no_such.status_code == 401
    assert wrong_pass.json()["detail"] == no_such.json()["detail"]


def test_disabled_user_cannot_log_in(
    client: TestClient, test_login: str, test_password: str
) -> None:
    from fire_safety_backend.infrastructure.db import connect

    with connect() as conn:
        conn.execute("UPDATE users SET disabled = 1 WHERE login = ?", (test_login,))
    assert auth.authenticate(test_login, test_password) is None


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


def test_session_cookie_is_not_readable_from_js(
    anon_client: TestClient, test_login: str, test_password: str
) -> None:
    """HttpOnly: XSS не утащит сессию."""
    r = anon_client.post("/api/auth/login", json={"login": test_login, "password": test_password})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_stale_session_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Забытая открытой вкладка на чужом компьютере не должна оставаться
    входом навсегда."""
    monkeypatch.setattr(auth, "SESSION_IDLE_HOURS", -1)
    assert client.get("/api/history").status_code == 401


def test_password_change_closes_open_sessions(client: TestClient, test_login: str) -> None:
    """Иначе тот, из-за кого пароль меняли, продолжает ходить по старой сессии."""
    assert client.get("/api/history").status_code == 200
    auth.set_password(test_login, "новый-длинный-пароль")
    assert client.get("/api/history").status_code == 401


def test_me_reports_whether_any_accounts_exist(anon_client: TestClient) -> None:
    """Свежий сервер без учётных записей обязан сказать это прямо, иначе любой
    ввод отвечает «неверный логин или пароль» и человек ищет ошибку в пароле."""
    body = anon_client.get("/api/auth/me").json()
    assert body["login"] is None
    assert body["any_users"] is True

    from fire_safety_backend.infrastructure.db import connect

    with connect() as conn:
        conn.execute("DELETE FROM users")
    assert anon_client.get("/api/auth/me").json()["any_users"] is False
