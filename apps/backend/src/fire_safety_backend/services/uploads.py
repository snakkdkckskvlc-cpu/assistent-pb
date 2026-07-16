"""Сервис приёма входа: файл или вставленный текст → строка."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .. import config
from ..infrastructure.parsers import UnsupportedFormatError, extract_text


async def text_from_input(file: UploadFile | None, text: str | None) -> str:
    """Извлекает текст из загруженного файла или возвращает вставленный текст.

    Приоритет — вставленный текст, если он непуст.
    """
    if text and text.strip():
        return text
    if not file:
        raise HTTPException(
            status_code=400,
            detail="Нужно передать либо файл, либо текст",
        )
    # Path(...).name защищает от path traversal через file.filename.
    safe_name = Path(file.filename).name if file.filename else "upload"
    dest = config.UPLOAD_DIR / safe_name
    dest.write_bytes(await file.read())
    try:
        # extract_text может запускать OCR (Tesseract) — тяжёлая блокирующая
        # операция, уводим с event loop.
        return await asyncio.to_thread(extract_text, dest)
    except UnsupportedFormatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
