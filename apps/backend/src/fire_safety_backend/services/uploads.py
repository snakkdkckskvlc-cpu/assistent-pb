"""Сервис приёма входа: файл или вставленный текст → строка."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .. import config
from ..infrastructure.parsers import UnsupportedFormatError, extract_text_with_meta


async def text_from_input_with_warning(
    file: UploadFile | None, text: str | None
) -> tuple[str, str]:
    """Текст из файла/поля + предупреждение о качестве источника.

    Второй элемент — пустая строка, когда текст пришёл из текстового слоя или
    вставлен руками, и человекочитаемое предупреждение, когда его пришлось
    распознавать со скана (см. parsers.ExtractionMeta.warning).
    """
    if text and text.strip():
        return text, ""
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
        content, meta = await asyncio.to_thread(extract_text_with_meta, dest)
    except UnsupportedFormatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return content, meta.warning


async def text_from_input(file: UploadFile | None, text: str | None) -> str:
    """Извлекает текст из загруженного файла или возвращает вставленный текст.

    Приоритет — вставленный текст, если он непуст.
    """
    return (await text_from_input_with_warning(file, text))[0]
