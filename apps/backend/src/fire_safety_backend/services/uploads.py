"""Сервис приёма входа: файл или вставленный текст → строка."""

from __future__ import annotations

import asyncio
import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

from .. import config
from ..infrastructure import secure_files
from ..infrastructure.parsers import UnsupportedFormatError, extract_text_with_meta

_READ_CHUNK = 1024 * 1024


async def read_limited(file: UploadFile) -> bytes:
    """Читает файл кусками, обрываясь на превышении потолка.

    Именно кусками, а не `await file.read()`: тот загрузит в память всё, что
    прислали, — и проверять размер ПОСЛЕ этого уже поздно, память съедена.

    Публичная: тем же потолком обязана пользоваться пакетная проверка
    (views/batch.py) — она принимает до 20 файлов за раз.
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


_PREFIX_RE = re.compile(r"^[0-9a-f]{8}_")


def unique_name(filename: str | None) -> str:
    """`Договор.docx` → `3f9a1c02_Договор.docx`.

    Без префикса двое сотрудников, приславших файлы с одинаковым именем,
    перетирают файл друг друга — и первый получает в анализ ЧУЖОЙ документ.
    На одном рабочем месте это было незаметно, на общем сервере это порча
    данных, а не неудобство.

    `Path(...).name` защищает от path traversal через имя файла.
    """
    safe = Path(filename).name if filename else "upload"
    return f"{uuid.uuid4().hex[:8]}_{safe}"


def original_name(path: Path | str) -> str:
    """Имя без служебного префикса — то, которое видит пользователь.

    Нужно там, где имя попадает человеку на глаза: заголовок скачиваемого
    файла, отчёт пакетной проверки. Показывать `3f9a1c02_Договор.docx` вместо
    `Договор.docx` — значит вываливать наружу деталь хранения.

    Оговорка: файл, реально названный по шаблону «восемь hex-символов и
    подчёркивание», потеряет это начало. Косметика, на содержимое не влияет.
    """
    name = Path(path).name
    return _PREFIX_RE.sub("", name, count=1)


async def text_from_input_with_source(
    file: UploadFile | None, text: str | None
) -> tuple[str, str, Path | None]:
    """Текст + предупреждение о качестве источника + путь к сохранённому файлу.

    Путь нужен проверке орфографии: она отдаёт исправленный документ КОПИЕЙ
    оригинала (с сохранением форматирования), а для этого нужен сам файл, а не
    только вытащенный из него текст. None — когда текст вставили руками.

    Отдаётся ЛОГИЧЕСКИЙ путь (`data/uploads/договор.docx`), даже если на диске
    лежит `договор.docx.enc`: получатель берёт из него `stem` и `suffix` —
    имя для результата и выбор парсера.
    """
    if text and text.strip():
        return text, "", None
    if not file:
        raise HTTPException(
            status_code=400,
            detail="Нужно передать либо файл, либо текст",
        )
    logical = config.UPLOAD_DIR / unique_name(file.filename)
    payload = await read_limited(file)
    try:
        secure_files.store(logical, payload)
    except secure_files.StorageUnprotected as e:
        # Шифрование обещано, но не работает. Отказываемся принимать документ,
        # а не кладём его на диск открытым текстом.
        raise HTTPException(status_code=500, detail=str(e)) from e
    try:
        # extract_text может запускать OCR (Tesseract) — тяжёлая блокирующая
        # операция, уводим с event loop. Работает по расшифрованной копии:
        # OCR и pdfplumber умеют только настоящий файл на диске.
        with secure_files.plaintext(logical) as readable:
            content, meta = await asyncio.to_thread(extract_text_with_meta, readable)
    except UnsupportedFormatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except secure_files.DecryptError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return content, meta.warning, logical


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
