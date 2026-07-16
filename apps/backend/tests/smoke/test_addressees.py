"""Smoke: CRUD-эндпоинты справочника адресатов (список/создание/удаление)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_list_returns_seeded_defaults(client: TestClient) -> None:
    r = client.get("/api/addressees")
    assert r.status_code == 200
    names = {a["name"] for a in r.json()}
    assert "заказчик" in names
    assert all(a["is_default"] for a in r.json() if a["name"] == "заказчик")


def test_create_addressee(client: TestClient) -> None:
    r = client.post("/api/addressees", json={"name": "тестовый адресат"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "тестовый адресат"
    assert body["is_default"] is False


def test_create_duplicate_case_insensitive_rejected(client: TestClient) -> None:
    r1 = client.post("/api/addressees", json={"name": "Дубликат"})
    assert r1.status_code == 201, r1.text
    r2 = client.post("/api/addressees", json={"name": "дубликат"})
    assert r2.status_code == 409


def test_create_blank_name_rejected(client: TestClient) -> None:
    r = client.post("/api/addressees", json={"name": "   "})
    assert r.status_code == 422


def test_delete_nondefault_addressee(client: TestClient) -> None:
    created = client.post("/api/addressees", json={"name": "удаляемый"}).json()
    r = client.delete(f"/api/addressees/{created['id']}")
    assert r.status_code == 204


def test_delete_default_addressee_forbidden(client: TestClient) -> None:
    defaults = [a for a in client.get("/api/addressees").json() if a["is_default"]]
    r = client.delete(f"/api/addressees/{defaults[0]['id']}")
    assert r.status_code == 403


def test_delete_nonexistent_addressee(client: TestClient) -> None:
    r = client.delete("/api/addressees/999999")
    assert r.status_code == 404
