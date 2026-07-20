"""Роутер: пакетная проверка договоров."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import config
from ..infrastructure.queue import queue
from ..pipelines import batch as pipelines

router = APIRouter(prefix="/api", tags=["batch"])

_MAX_FILES = 20


@router.post("/batch")
async def api_batch(files: list[UploadFile] = File(...)) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="Не передано ни одного файла")
    if len(files) > _MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Слишком много файлов (максимум {_MAX_FILES})")

    # Сохраняем на диск здесь, а тяжёлый разбор (парсинг/OCR/LLM) — в задаче.
    paths: list[Path] = []
    for f in files:
        # Path(...).name защищает от path traversal через имя файла.
        safe_name = Path(f.filename).name if f.filename else "upload"
        dest = config.UPLOAD_DIR / safe_name
        dest.write_bytes(await f.read())
        paths.append(dest)

    task = await queue.submit("batch", lambda t: pipelines.run_batch(paths, task=t))
    return {"task_id": task.id}
