"""Smoke: /api/health отвечает и содержит ожидаемую схему."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["ollama"]["model"] == "test-model"
    assert "rag_ready" in data
    # LanguageTool не поднят в тестах — эндпоинт не должен падать, просто False.
    assert data["languagetool_ready"] is False
