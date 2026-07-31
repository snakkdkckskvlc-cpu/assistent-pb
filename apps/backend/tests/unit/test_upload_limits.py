"""Потолок на размер загружаемого файла.

Без него `await file.read()` втягивает в память всё, что прислали: случайно
перетащенный архив или видео укладывает backend без внятного сообщения —
пользователь видит только «приложение не отвечает».

Ключевая деталь реализации: читать надо КУСКАМИ. Проверка размера после
`read()` бесполезна — память уже съедена.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile
from fire_safety_backend import config
from fire_safety_backend.services.uploads import read_limited


def _upload(data: bytes) -> UploadFile:
    return UploadFile(filename="doc.docx", file=io.BytesIO(data))


async def test_normal_file_passes() -> None:
    payload = b"x" * 1024
    assert await read_limited(_upload(payload)) == payload


async def test_file_at_limit_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 4096)
    payload = b"x" * 4096
    assert len(await read_limited(_upload(payload))) == 4096


async def test_oversized_file_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 4096)
    with pytest.raises(HTTPException) as exc:
        await read_limited(_upload(b"x" * 4097))
    assert exc.value.status_code == 413


async def test_rejection_does_not_buffer_whole_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """Обрыв обязан произойти по ходу чтения, а не после него.

    Проверяем через счётчик прочитанного: файл сильно больше потолка не
    должен быть вычитан целиком.
    """
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1024)
    read_total = 0

    class _CountingFile(io.BytesIO):
        def read(self, size: int = -1) -> bytes:  # type: ignore[override]
            nonlocal read_total
            data = super().read(size)
            read_total += len(data)
            return data

    huge = _CountingFile(b"x" * (20 * 1024 * 1024))
    with pytest.raises(HTTPException):
        await read_limited(UploadFile(filename="big.bin", file=huge))

    assert read_total < 20 * 1024 * 1024, "файл вычитан целиком — потолок не спасает память"


def test_default_limit_is_sane() -> None:
    """Реальные документы (договор со сканами) — единицы мегабайт."""
    assert 8 * 1024 * 1024 <= config.MAX_UPLOAD_BYTES <= 256 * 1024 * 1024


class TestBatchUsesTheSameLimit:
    """Пакетная проверка обязана считаться тем же потолком.

    Она этого не делала: `await f.read()` без ограничений, до 20 файлов за
    запрос. То есть ограничение, ради которого потолок и вводился, обходилось
    сменой одной кнопки в интерфейсе. Тест существует именно поэтому — раньше
    покрытия у этой ветки не было, и дыра жила незамеченной.
    """

    def test_oversized_file_in_batch_is_rejected(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Через настоящий эндпоинт, а не через вызов функции: дыра была именно
        в роутере, который потолок не применял."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from fire_safety_backend.services.auth import User
        from fire_safety_backend.views import auth
        from fire_safety_backend.views.batch import router

        monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 4096)
        # UPLOAD_DIR обязательно во временный каталог: первый файл пакета
        # успевает сохраниться до отказа на втором, и без подмены тест писал бы
        # в настоящий data/uploads.
        monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[auth.current_user] = lambda: User(
            id=1, login="tester", is_admin=False
        )
        client = TestClient(app)

        r = client.post(
            "/api/batch",
            files=[
                ("files", ("маленький.docx", b"x" * 100)),
                ("files", ("огромный.pdf", b"x" * 8192)),
            ],
        )

        assert r.status_code == 413, "пакетная проверка приняла файл больше потолка"

    def test_oversized_file_is_not_saved(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Отказ обязан случиться ДО записи на диск: иначе потолок не спасает
        ни память, ни место."""
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from fire_safety_backend.services.auth import User
        from fire_safety_backend.views import auth
        from fire_safety_backend.views.batch import router

        monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 4096)
        monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
        config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[auth.current_user] = lambda: User(
            id=1, login="tester", is_admin=False
        )

        TestClient(app).post("/api/batch", files=[("files", ("огромный.pdf", b"x" * 8192))])

        assert list(config.UPLOAD_DIR.iterdir()) == []
