"""Smoke: завершённые задачи попадают в /api/history, история чистится.

Запись идёт асинхронно из воркера очереди (queue.on_task_finished) уже
ПОСЛЕ того, как задача видна клиенту как done — поэтому появление строки
в истории тоже опрашивается с дедлайном, а не проверяется мгновенно.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        return {"errors": [], "corrected_text": user, "stats": {}}

    from fire_safety_backend.infrastructure import languagetool, llm

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    async def fake_lt_check(text: str, language: str = "ru-RU") -> list[dict]:
        return []

    monkeypatch.setattr(languagetool, "check", fake_lt_check)


def _wait_task_done(client: TestClient, task_id: str, timeout_s: float = 5) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        data = client.get(f"/api/tasks/{task_id}").json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError(f"Task {task_id} not finished in {timeout_s}s")


def _wait_history_row(client: TestClient, task_id: str, timeout_s: float = 3) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        rows = client.get("/api/history").json()
        for row in rows:
            if row["task_id"] == task_id:
                return row
        time.sleep(0.05)
    raise AssertionError(f"Task {task_id} not in history after {timeout_s}s")


def test_finished_task_recorded_in_history(client: TestClient) -> None:
    r = client.post("/api/spellcheck", data={"text": "Тестовый текст."})
    task_id = r.json()["task_id"]
    _wait_task_done(client, task_id)

    row = _wait_history_row(client, task_id)
    assert row["kind"] == "spellcheck"
    assert row["status"] == "done"
    assert row["created_at"]
    assert row["finished_at"]
    assert row["duration_sec"] is not None


def test_history_clear(client: TestClient) -> None:
    r = client.post("/api/spellcheck", data={"text": "Ещё один текст."})
    task_id = r.json()["task_id"]
    _wait_task_done(client, task_id)
    _wait_history_row(client, task_id)

    assert client.delete("/api/history").status_code == 204
    assert client.get("/api/history").json() == []
