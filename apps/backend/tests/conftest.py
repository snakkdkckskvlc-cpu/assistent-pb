"""Общие фикстуры для тестов backend'а."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Учётная запись, под которой ходят все тесты. Пароля нет: вход в приложении
# идёт только по логину (см. services/auth.py).
TEST_LOGIN = "tester"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
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

    from fire_safety_backend.infrastructure import db as db_module

    tmp_db = Path(tempfile.mkdtemp()) / "test_app.db"
    monkeypatch.setattr(db_module, "DB_PATH", tmp_db)

    # И от реальных рабочих каталогов. Две отдельные причины, каждой хватает:
    #   1. Тесты создавали файлы в настоящем data/outputs — так там и завёлся
    #      «документ_исправленный.docx» с текстом из test_history.py.
    #   2. Lifespan запускает автоочистку (services/retention.py), а она
    #      удаляет файлы старше DATA_RETENTION_DAYS. Без подмены прогон тестов
    #      выносил бы документы пользователя.
    # Подменять НАДО до входа в TestClient: lifespan стартует именно там.
    from fire_safety_backend import config

    for name in ("UPLOAD_DIR", "OUTPUT_DIR", "WORK_DIR"):
        target = tmp_path / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, name, target)

    from fire_safety_backend.main import app
    from fire_safety_backend.services import auth as auth_service

    # Калитка целостности проверяет манифест при старте backend'а. В рабочем
    # дереве он почти всегда расходится с кодом (правки ещё не закоммичены), и
    # без этого тесты падали бы после каждой строчки. Настоящую проверку
    # манифеста делает шаг --check в CI, на уже закоммиченном дереве.
    monkeypatch.setenv("ASSISTENT_PB_DEV", "1")

    with TestClient(app) as tc:
        # Роутеры закрыты авторизацией, поэтому клиент сразу входит: иначе
        # каждый smoke-тест проверял бы не свою функцию, а 401. Пользователь
        # создаётся ПОСЛЕ входа в TestClient — таблицы появляются в lifespan.
        auth_service.create_user(TEST_LOGIN)
        r = tc.post("/api/auth/login", json={"login": TEST_LOGIN})
        assert r.status_code == 200, f"тестовый вход не удался: {r.text}"
        yield tc


@pytest.fixture
def test_login() -> str:
    """Логин тестовой учётной записи. Фикстурой, а не импортом константы:
    каталог тестов не пакет, и `from ..conftest import ...` тут не работает."""
    return TEST_LOGIN


@pytest.fixture
def anon_client(client: TestClient) -> Iterator[TestClient]:
    """Тот же сервер, но без входа — для проверок «а закрыто ли»."""
    client.cookies.clear()
    yield client
