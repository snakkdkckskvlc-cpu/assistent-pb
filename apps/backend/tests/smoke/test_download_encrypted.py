"""Сквозной путь скачивания: на диске зашифровано — у пользователя целый файл.

Здесь проверяется стык, на котором легко потерять данные молча: раньше
`/api/download` отдавал файл через FileResponse, а теперь собирает ответ сам —
значит и имя файла в заголовке собирает сам. Кириллическое имя
(`документ_исправленный.docx`) в `filename=` не помещается по стандарту, и без
`filename*` пользователь получил бы файл с искажённым именем.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient
from fire_safety_backend import config
from fire_safety_backend.infrastructure import secure_files


class _XorProtector:
    name = "xor"

    def protect(self, data: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in data)

    def unprotect(self, data: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in data)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "tmp")
    for d in (config.OUTPUT_DIR, config.WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)
    secure_files.use_protector(_XorProtector())
    yield
    secure_files.reset()


@pytest.fixture
def client() -> TestClient:
    from fastapi import FastAPI
    from fire_safety_backend.views.downloads import router

    # Только роутер скачивания: поднимать всё приложение (очередь, БД, LLM)
    # для проверки одного эндпоинта незачем.
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_encrypted_file_is_served_decrypted(client: TestClient) -> None:
    name = "документ_исправленный.docx"
    payload = "Содержимое исправленного документа".encode()
    stored = secure_files.store(config.OUTPUT_DIR / name, payload)
    assert stored.name.endswith(".enc")

    r = client.get(f"/api/download/{name}")

    assert r.status_code == 200
    assert r.content == payload
    assert "wordprocessingml" in r.headers["content-type"]


def test_cyrillic_filename_survives_the_header(client: TestClient) -> None:
    name = "документ_исправленный.docx"
    secure_files.store(config.OUTPUT_DIR / name, b"x")

    disposition = client.get(f"/api/download/{name}").headers["content-disposition"]

    assert disposition.startswith("attachment;")
    assert "filename*=utf-8''" in disposition
    encoded = disposition.split("filename*=utf-8''", 1)[1]
    assert unquote(encoded) == name


def test_ascii_filename_uses_plain_form(client: TestClient) -> None:
    name = "letter_ab12cd34.docx"
    secure_files.store(config.OUTPUT_DIR / name, b"x")

    disposition = client.get(f"/api/download/{name}").headers["content-disposition"]

    assert disposition == f'attachment; filename="{name}"'


def test_legacy_plaintext_file_still_downloads(client: TestClient) -> None:
    """Файлы, сгенерированные до появления шифрования, должны отдаваться."""
    name = "старое_письмо.docx"
    (config.OUTPUT_DIR / name).write_bytes("старый файл".encode())

    r = client.get(f"/api/download/{name}")

    assert r.status_code == 200
    assert r.content == "старый файл".encode()


def test_missing_file_is_404(client: TestClient) -> None:
    assert client.get("/api/download/нет-такого.docx").status_code == 404


def test_undecryptable_file_gives_error_not_garbage(client: TestClient) -> None:
    """Файл, зашифрованный другой учётной записью, нельзя отдать «как есть»:
    пользователь получил бы мусор с расширением .docx."""
    name = "чужой.docx"
    secure_files.store(config.OUTPUT_DIR / name, b"secret")

    class _Failing(_XorProtector):
        def unprotect(self, data: bytes) -> bytes:
            raise OSError("не та учётная запись")

    secure_files.use_protector(_Failing())
    r = client.get(f"/api/download/{name}")

    assert r.status_code == 500
    assert "учётной записи" in r.json()["detail"]


def test_path_traversal_still_blocked(client: TestClient, tmp_path: Path) -> None:
    outside = tmp_path / "секрет.docx"
    outside.write_bytes("не отдавать".encode())

    r = client.get("/api/download/..%2F..%2F%D1%81%D0%B5%D0%BA%D1%80%D0%B5%D1%82.docx")

    assert r.status_code == 404
