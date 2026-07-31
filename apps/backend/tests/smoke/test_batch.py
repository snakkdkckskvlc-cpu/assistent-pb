"""Smoke: пакетная проверка — классификация + юр. анализ только договоров.

LLM и RAG замоканы; классификатор (services/classify.py) работает по-настоящему —
тексты фикстур содержат реальные маркеры договора и письма.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

_CONTRACT_TEXT = """ДОГОВОР ПОДРЯДА №14/2026

ООО «ПожСервис», именуемое в дальнейшем «Подрядчик», и ПАО «НЛМК»,
именуемое в дальнейшем «Заказчик», заключили настоящий договор.

1. ПРЕДМЕТ ДОГОВОРА
1.1. Подрядчик обязуется выполнить техническое обслуживание систем АПС.

2. ОБЯЗАННОСТИ СТОРОН
2.1. Штраф за просрочку составляет 10% за каждый день.
"""

_LETTER_TEXT = """Уважаемый Сергей Сергеевич!

Доводим до Вашего сведения, что плановое обслуживание назначено на август.
Просим Вас согласовать даты допуска бригады.

С уважением, Директор О.Н. Сляднев
"""


@pytest.fixture(autouse=True)
def _mock_llm_and_rag(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        return {
            "находки": [
                {
                    "критичность": "красный",
                    "цитата_из_договора": "Штраф за просрочку составляет 10%",
                    "в_чём_риск": "несоразмерная неустойка",
                    "ссылка_на_норму": "ст. 333 ГК РФ",
                    "предложение_правки": "снизить до 0.1%",
                }
            ],
            "сводка": {
                "плюсы_для_компании": [],
                "минусы_для_компании": [],
                "общий_вывод": "Требует правок",
            },
        }

    from fire_safety_backend.infrastructure import llm

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    from fire_safety_backend.pipelines import legal as pipelines_legal

    # И гибридный, и векторный путь — иначе тест уйдёт в настоящий ChromaDB.
    for name in ("retrieve_hybrid", "retrieve_many"):
        monkeypatch.setattr(
            pipelines_legal,
            name,
            lambda queries, top_k=None, where=None, domain=None: [[] for _ in queries],
        )

    from fire_safety_backend import config

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "up")
    (tmp_path / "out").mkdir()
    (tmp_path / "up").mkdir()


def _wait_done(client: TestClient, task_id: str, timeout_s: float = 10) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        data = client.get(f"/api/tasks/{task_id}").json()
        if data["status"] in ("done", "error"):
            return data
        time.sleep(0.05)
    raise AssertionError("batch task not finished")


def test_batch_analyzes_contracts_and_skips_letters(client: TestClient) -> None:
    r = client.post(
        "/api/batch",
        files=[
            ("files", ("dogovor.txt", _CONTRACT_TEXT.encode(), "text/plain")),
            ("files", ("pismo.txt", _LETTER_TEXT.encode(), "text/plain")),
        ],
    )
    assert r.status_code == 200, r.text
    result = _wait_done(client, r.json()["task_id"])
    assert result["status"] == "done", result

    payload = result["result"]
    assert payload["stats"] == {"всего": 2, "договоров": 1, "пропущено": 1}
    assert payload.get("_docx_path"), "Должен быть сводный DOCX-отчёт"

    by_name = {f["файл"]: f for f in payload["файлы"]}
    contract = by_name["dogovor.txt"]
    assert contract["тип"] == "договор"
    assert contract["пропущен"] is False
    assert contract["находки"][0]["критичность"] == "красный"

    letter = by_name["pismo.txt"]
    assert letter["тип"] == "письмо"
    assert letter["пропущен"] is True
    assert "юр. анализ не запускался" in letter["причина"]


def test_batch_rejects_empty_upload(client: TestClient) -> None:
    r = client.post("/api/batch", files=[])
    assert r.status_code == 422  # FastAPI: обязательное поле files не передано


def test_batch_rejects_too_many_files(client: TestClient) -> None:
    files = [("files", (f"f{i}.txt", b"x", "text/plain")) for i in range(21)]
    r = client.post("/api/batch", files=files)
    assert r.status_code == 400
