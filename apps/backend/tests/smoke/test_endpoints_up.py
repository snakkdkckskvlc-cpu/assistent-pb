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
            "получатель": "Руководителю организации\n\n ",
            "обращение": "Уважаемые коллеги!",
            "тело": "Тестовое письмо.",
            "должность_отправителя_placeholder": "[должность]",
            "фио_отправителя_placeholder": "[Фамилия И.О.]",
        }

    from fire_safety_backend.infrastructure import llm

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    # RAG: пусто, без обращения к ChromaDB
    from fire_safety_backend.pipelines import legal as pipelines_legal
    from fire_safety_backend.pipelines import letter as pipelines_letter

    monkeypatch.setattr(
        pipelines_legal, "retrieve_many", lambda queries, top_k=None: [[] for _ in queries]
    )
    monkeypatch.setattr(pipelines_letter, "retrieve_letters", lambda query, top_k=2: [])

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

    # LanguageTool — отдельный процесс, в тестах не поднят. Пустой список
    # соответствует реальному поведению клиента, когда сервер недоступен
    # (см. infrastructure/languagetool.py::check).
    from fire_safety_backend.infrastructure import languagetool

    async def fake_lt_check(text: str, language: str = "ru-RU") -> list[dict]:
        return []

    monkeypatch.setattr(languagetool, "check", fake_lt_check)


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
    # Полоса загрузки для UI (см. infrastructure/queue.py::Task.percent) должна
    # быть в ответе валидным числом 0..100 даже когда LLM замокан и реального
    # стриминга не было (конкретное значение зависит от того, где по ходу
    # пайплайна выставляются промежуточные метки — не фиксируем его жёстко).
    assert isinstance(result["percent"], int)
    assert 0 <= result["percent"] <= 100


def test_legal_accepts_text(client: TestClient) -> None:
    r = client.post("/api/legal", data={"text": "Договор №1 — тестовый."})
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    result = _wait_task_done(client, task_id)
    assert result["status"] == "done", result
    assert "находки" in result["result"]


def test_letter_accepts_draft(client: TestClient) -> None:
    # run_letter() отдаёт только текстовые поля — DOCX здесь не собирается,
    # интерфейс показывает поля редактируемыми и собирает DOCX отдельным
    # запросом на /api/letter/render (см. test_letter_render_* ниже).
    r = client.post(
        "/api/letter",
        json={"draft": "Напомнить о встрече", "addressee_type": "заказчик"},
    )
    assert r.status_code == 200
    task_id = r.json()["task_id"]
    result = _wait_task_done(client, task_id)
    assert result["status"] == "done", result
    payload = result["result"]
    assert payload["тема"] == "test"
    assert payload["тело"] == "Тестовое письмо."
    assert "_docx_path" not in payload
    assert "email" not in payload


def test_letter_render_builds_docx_from_fields(client: TestClient) -> None:
    r = client.post(
        "/api/letter/render",
        json={
            "тема": "О проведении ТО",
            "получатель": "Директору\nООО «Ромашка»\n\nИванову И.И.",
            "обращение": "Уважаемый Иван Иванович!",
            "тело": "Текст письма.",
            "должность_отправителя_placeholder": "Директор",
            "фио_отправителя_placeholder": "О.Н. Сляднев",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["docx_path"].endswith(".docx")


def test_letter_render_accepts_empty_fields(client: TestClient) -> None:
    # Все поля опциональны — пустой запрос не должен падать с 422/500,
    # просто соберёт бланк с пустыми местами (пользователь мог всё стереть).
    r = client.post("/api/letter/render", json={})
    assert r.status_code == 200, r.text


def test_reject_empty_input(client: TestClient) -> None:
    r = client.post("/api/spellcheck", data={"text": "   "})
    assert r.status_code in (400, 422)


def test_legal_grounds_citations_against_retrieved_chunks(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Находка со ссылкой на реально отданный чанк — подтверждена; на
    выдуманный ID — нет. Цитата, реально входящая в текст, — найдена;
    выдуманная — нет."""
    from fire_safety_backend.infrastructure import llm
    from fire_safety_backend.pipelines import legal as pipelines_legal

    # Фиксируем короткий ID вместо того, чтобы выковыривать его из
    # отрендеренного промпта регэкспом — тест не должен зависеть от формата
    # промпта (code-review, находка №14).
    monkeypatch.setattr(pipelines_legal, "generate_short_id", lambda seed, length=4: "GGVR")

    contract_text = "Договор №1. Штраф за просрочку составляет 0.5% в день."

    def fake_retrieve_many(queries: list[str], top_k=None) -> list[list[dict]]:
        return [[{"text": "норма про штрафы", "source": "123-ФЗ.txt", "score": 0.9}]] + [
            [] for _ in queries[1:]
        ]

    monkeypatch.setattr(pipelines_legal, "retrieve_many", fake_retrieve_many)

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        return {
            "находки": [
                {
                    "критичность": "жёлтый",
                    "цитата_из_договора": "Штраф за просрочку составляет 0.5% в день.",
                    "в_чём_риск": "тест",
                    "ссылка_на_норму": "ст. 1 123-ФЗ",
                    "источник_фрагмента": "GGVR",
                    "предложение_правки": "тест",
                },
                {
                    "критичность": "красный",
                    "цитата_из_договора": "Текста, которого нет в договоре, тут быть не может",
                    "в_чём_риск": "тест",
                    "ссылка_на_норму": "выдуманная статья",
                    "источник_фрагмента": "ZZZZ",
                    "предложение_правки": "тест",
                },
            ],
            "сводка": {"плюсы_для_компании": [], "минусы_для_компании": [], "общий_вывод": "OK"},
        }

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    r = client.post("/api/legal", data={"text": contract_text})
    task_id = r.json()["task_id"]
    result = _wait_task_done(client, task_id)
    findings = result["result"]["находки"]

    assert findings[0]["_источник_подтверждён"] is True
    assert findings[0]["_источник_файл"] == "123-ФЗ.txt"
    assert findings[0]["_цитата_найдена"] is True

    assert findings[1]["_источник_подтверждён"] is False
    assert findings[1]["_цитата_найдена"] is False
