"""Сервис приёма входа: файл или вставленный текст → строка."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .. import config
from ..infrastructure.parsers import UnsupportedFormatError, extract_text_with_meta

_READ_CHUNK = 1024 * 1024


async def _read_limited(file: UploadFile) -> bytes:
    """Читает файл кусками, обрываясь на превышении потолка.

    Именно кусками, а не `await file.read()`: тот загрузит в память всё, что
    прислали, — и проверять размер ПОСЛЕ этого уже поздно, память съедена.
    """
    parts: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > config.MAX_UPLOAD_BYTES:
            limit_mb = config.MAX_UPLOAD_BYTES / 1024 / 1024
            raise HTTPException(
                status_code=413,
                detail=f"Файл больше {limit_mb:.0f} МБ — такие документы не обрабатываются",
            )
        parts.append(chunk)
    return b"".join(parts)


async def text_from_input_with_source(
    file: UploadFile | None, text: str | None
) -> tuple[str, str, Path | None]:
    """Текст + предупреждение о качестве источника + путь к сохранённому файлу.

    Путь нужен проверке орфографии: она отдаёт исправленный документ КОПИЕЙ
    оригинала (с сохранением форматирования), а для этого нужен сам файл, а не
    только вытащенный из него текст. None — когда текст вставили руками.
    """
    if text and text.strip():
        return text, "", None
    if not file:
        raise HTTPException(
            status_code=400,
            detail="Нужно передать либо файл, либо текст",
        )
    # Path(...).name защищает от path traversal через file.filename.
    safe_name = Path(file.filename).name if file.filename else "upload"
    dest = config.UPLOAD_DIR / safe_name
    payload = await _read_limited(file)
    dest.write_bytes(payload)
    try:
        # extract_text может запускать OCR (Tesseract) — тяжёлая блокирующая
        # операция, уводим с event loop.
        content, meta = await asyncio.to_thread(extract_text_with_meta, dest)
    except UnsupportedFormatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return content, meta.warning, dest


async def text_from_input_with_warning(
    file: UploadFile | None, text: str | None
) -> tuple[str, str]:
    """Текст из файла/поля + предупреждение о качестве источника.

    Второй элемент — пустая строка, когда текст пришёл из текстового слоя или
    вставлен руками, и человекочитаемое предупреждение, когда его пришлось
    распознавать со скана (см. parsers.ExtractionMeta.warning).
    """
    content, warning, _ = await text_from_input_with_source(file, text)
    return content, warning


async def text_from_input(file: UploadFile | None, text: str | None) -> str:
    """Извлекает текст из загруженного файла или возвращает вставленный текст.

    Приоритет — вставленный текст, если он непуст.
    """
    return (await text_from_input_with_warning(file, text))[0]
