"""Smoke: POST /api/feedback пишет строку в БД (см. services/feedback.py)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from fire_safety_backend.infrastructure import db


def test_create_feedback_writes_row(client: TestClient) -> None:
    r = client.post(
        "/api/feedback",
        json={"function": "spellcheck", "task_id": "abc123", "rating": "up", "comment": "супер"},
    )
    assert r.status_code == 201, r.text
    assert r.json() == {"ok": True}

    with db.connect() as conn:
        row = conn.execute(
            "SELECT function, task_id, rating, comment FROM feedback WHERE task_id = ?",
            ("abc123",),
        ).fetchone()
    assert row is not None
    assert row["function"] == "spellcheck"
    assert row["rating"] == "up"
    assert row["comment"] == "супер"


def test_create_feedback_comment_defaults_to_empty(client: TestClient) -> None:
    r = client.post("/api/feedback", json={"function": "legal", "task_id": "xyz", "rating": "down"})
    assert r.status_code == 201, r.text

    with db.connect() as conn:
        row = conn.execute("SELECT comment FROM feedback WHERE task_id = ?", ("xyz",)).fetchone()
    assert row["comment"] == ""


def test_create_feedback_invalid_rating_rejected(client: TestClient) -> None:
    r = client.post(
        "/api/feedback",
        json={"function": "letter", "task_id": "t1", "rating": "sideways"},
    )
    assert r.status_code == 422


def test_create_feedback_missing_task_id_rejected(client: TestClient) -> None:
    r = client.post("/api/feedback", json={"function": "letter", "rating": "up"})
    assert r.status_code == 422
