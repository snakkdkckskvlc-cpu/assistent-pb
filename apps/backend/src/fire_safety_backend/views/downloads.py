"""Роутер: скачивание сгенерированных файлов."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import config

router = APIRouter(prefix="/api/download", tags=["download"])


@router.get("/{filename}")
async def api_download(filename: str) -> FileResponse:
    safe = Path(filename).name  # защита от path traversal
    path = config.OUTPUT_DIR / safe
    if not path.exists():
        raise HTTPException(status_code=404, detail="Файл не найден")
    return FileResponse(
        path=str(path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=safe,
    )
