"""Роутер: юридический анализ договора."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..infrastructure.queue import queue
from ..pipelines import legacy as pipelines
from ..services import text_from_input

router = APIRouter(prefix="/api", tags=["legal"])


@router.post("/legal")
async def api_legal(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
) -> dict:
    content = await text_from_input(file, text)
    if not content.strip():
        raise HTTPException(status_code=400, detail="Пустой текст договора")
    task = await queue.submit(
        "legal",
        lambda t: pipelines.run_legal_analysis(content, task=t),
    )
    return {"task_id": task.id}
