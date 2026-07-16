"""Общие фикстуры для тестов backend'а."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """FastAPI TestClient с замоканной Ollama-проверкой.

    Тесты не должны требовать запущенной Ollama или наличия модели —
    healthcheck возвращает заглушку. Клиент используется через `with`,
    чтобы FastAPI-lifespan запустил очередь задач.
    """

    async def fake_healthcheck() -> dict:
        return {
            "ok": True,
            "model": "test-model",
            "installed": ["test-model"],
            "warning": None,
        }

    from fire_safety_backend.infrastructure import llm

    monkeypatch.setattr(llm, "healthcheck", fake_healthcheck)

    # Аналогично для LanguageTool — тест не должен зависеть от того, поднят
    # ли sidecar на машине разработчика в момент прогона тестов.
    async def fake_lt_healthcheck() -> dict:
        return {"ok": False}

    from fire_safety_backend.infrastructure import languagetool

    monkeypatch.setattr(languagetool, "healthcheck", fake_lt_healthcheck)

    # Изолируем тесты от реальной data/app.db — иначе прогон тестов сеет
    # и трогает боевую базу справочника адресатов.
    import tempfile
    from pathlib import Path

    from fire_safety_backend.infrastructure import db as db_module

    tmp_db = Path(tempfile.mkdtemp()) / "test_app.db"
    monkeypatch.setattr(db_module, "DB_PATH", tmp_db)

    from fire_safety_backend.main import app

    with TestClient(app) as tc:
        yield tc
