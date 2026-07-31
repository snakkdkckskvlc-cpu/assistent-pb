"""Роутер: пакетная проверка договоров."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import config
from ..infrastructure import secure_files
from ..infrastructure.queue import queue
from ..pipelines import batch as pipelines
from ..services.uploads import read_limited
from . import auth

router = APIRouter(prefix="/api", tags=["batch"])

_MAX_FILES = 20


@router.post("/batch")
async def api_batch(
    files: list[UploadFile] = File(...), user: auth.User = Depends(auth.current_user)
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="Не передано ни одного файла")
    if len(files) > _MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Слишком много файлов (максимум {_MAX_FILES})")

    # Сохраняем на диск здесь, а тяжёлый разбор (парсинг/OCR/LLM) — в задаче.
    # Через read_limited, а не `await f.read()`: потолок MAX_UPLOAD_BYTES был
    # только в одиночной загрузке, и пакетная принимала до 20 файлов любого
    # размера — то есть обходила ограничение, ради которого он и вводился.
    paths: list[Path] = []
    for f in files:
        # Path(...).name защищает от path traversal через имя файла.
        safe_name = Path(f.filename).name if f.filename else "upload"
        logical = config.UPLOAD_DIR / safe_name
        payload = await read_limited(f)
        try:
            secure_files.store(logical, payload)
        except secure_files.StorageUnprotected as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
        paths.append(logical)

    task = await queue.submit(
        "batch", lambda t: pipelines.run_batch(paths, task=t), owner=user.login
    )
    return {"task_id": task.id}
