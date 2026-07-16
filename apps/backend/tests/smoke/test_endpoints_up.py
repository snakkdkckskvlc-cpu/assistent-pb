"""Smoke: три POST-эндпоинта принимают запросы и ставят задачу в очередь.

LLM, RAG и генератор DOCX замоканы — реальные вызовы Ollama/ChromaDB не делаются.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _mock_pipeline_dependencies(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        return {
            "errors": [],
            "corrected_text": user,
            "находки": [],
            "сводка": {
                "плюсы_для_компании": [],
                "минусы_для_компании": [],
                "общий_вывод": "OK",
            },
            "тема": "test",
            "обращение": "Уважаемые коллеги!",
            "тело": "Тестовое письмо.",
            "формула_вежливости": "С уважением,",
            "должность_отправителя_placeholder": "[должность]",
            "фио_отправителя_placeholder": "[Фамилия И.О.]",
            "email": {
                "кому": "test@example.com",
                "тема": "test",
                "тело": "Тестовое сопроводительное письмо.",
            },
        }

    from fire_safety_backend.infrastructure import llm

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    # RAG: пусто, без обращения к ChromaDB
    from fire_safety_backend.pipelines import legacy as pipelines_legacy

    monkeypatch.setattr(
        pipelines_legacy, "retrieve_many", lambda queries, top_k=None: [[] for _ in queries]
    )

    # Генератор DOCX: просто создаём файл-заглушку
    def fake_build_letter_docx(letter: dict, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake docx")
        return output_path

    from fire_safety_backend.infrastructure.generators import letter_docx

    monkeypatch.setattr(letter_docx, "build_letter_docx", fake_build_letter_docx)

    # Пишем в tmp_path, а не в реальный data/outputs/.
    from fire_safety_backend import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)


def _wait_task_done(client: TestClient, task_id: str, timeout_s: float = 5) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = client.get(f"/api/tasks/{task_id}")
        assert r.status_code == 200
        data = r.json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError(f"Task {task_id} not finished in {timeout_s}s")


def test_spellcheck_accepts_text(client: TestClient) -> None:
    r = client.post("/api/spellcheck", data={"text": "Тестовый текст."})
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    result = _wait_task_done(client, task_id)
    assert result["status"] == "done", result
    assert "errors" in result["result"]


def test_legal_accepts_text(client: TestClient) -> None:
    r = client.post("/api/legal", data={"text": "Договор №1 — тестовый."})
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    result = _wait_task_done(client, task_id)
    assert result["status"] == "done", result
    assert "находки" in result["result"]


def test_letter_accepts_draft(client: TestClient) -> None:
    r = client.post(
        "/api/letter",
        json={"draft": "Напомнить о встрече", "addressee_type": "заказчик"},
    )
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    result = _wait_task_done(client, task_id)
    assert result["status"] == "done", result
    payload = result["result"]
    assert "тема" in payload
    # Проверяем что появилось сопроводительное e-mail
    assert "email" in payload
    assert "тело" in payload["email"]
    # DOCX сгенерирован
    assert payload.get("_docx_path"), "Должен быть путь к DOCX для скачивания"


def test_reject_empty_input(client: TestClient) -> None:
    r = client.post("/api/spellcheck", data={"text": "   "})
    assert r.status_code in (400, 422)
