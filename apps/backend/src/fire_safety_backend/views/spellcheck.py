"""Роутер: проверка документа на ошибки."""
from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..infrastructure.queue import queue
from ..pipelines import legacy as pipelines
from ..services import text_from_input

router = APIRouter(prefix="/api", tags=["spellcheck"])


@router.post("/spellcheck")
async def api_spellcheck(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
) -> dict:
    content = await text_from_input(file, text)
    if not content.strip():
        raise HTTPException(status_code=400, detail="Пустой текст")
    task = await queue.submit(
        "spellcheck",
        lambda t: pipelines.run_spellcheck(content, task=t),
    )
    return {"task_id": task.id}
