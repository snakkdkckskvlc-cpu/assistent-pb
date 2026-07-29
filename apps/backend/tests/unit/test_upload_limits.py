"""Потолок на размер загружаемого файла.

Без него `await file.read()` втягивает в память всё, что прислали: случайно
перетащенный архив или видео укладывает backend без внятного сообщения —
пользователь видит только «приложение не отвечает».

Ключевая деталь реализации: читать надо КУСКАМИ. Проверка размера после
`read()` бесполезна — память уже съедена.
"""

from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile
from fire_safety_backend import config
from fire_safety_backend.services.uploads import _read_limited


def _upload(data: bytes) -> UploadFile:
    return UploadFile(filename="doc.docx", file=io.BytesIO(data))


async def test_normal_file_passes() -> None:
    payload = b"x" * 1024
    assert await _read_limited(_upload(payload)) == payload


async def test_file_at_limit_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 4096)
    payload = b"x" * 4096
    assert len(await _read_limited(_upload(payload))) == 4096


async def test_oversized_file_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 4096)
    with pytest.raises(HTTPException) as exc:
        await _read_limited(_upload(b"x" * 4097))
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
        await _read_limited(UploadFile(filename="big.bin", file=huge))

    assert read_total < 20 * 1024 * 1024, "файл вычитан целиком — потолок не спасает память"


def test_default_limit_is_sane() -> None:
    """Реальные документы (договор со сканами) — единицы мегабайт."""
    assert 8 * 1024 * 1024 <= config.MAX_UPLOAD_BYTES <= 256 * 1024 * 1024
