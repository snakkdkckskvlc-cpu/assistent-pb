"""Роутер: проверка документа на ошибки."""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..infrastructure.queue import queue
from ..pipelines import spellcheck as pipelines
from ..services import text_from_input_with_warning

router = APIRouter(prefix="/api", tags=["spellcheck"])


@router.post("/spellcheck")
async def api_spellcheck(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
) -> dict:
    content, source_warning = await text_from_input_with_warning(file, text)
    if not content.strip():
        raise HTTPException(status_code=400, detail="Пустой текст")

    async def run(task) -> dict:
        result = await pipelines.run_spellcheck(content, task=task)
        # Орфография распознанного скана — это в основном ошибки Tesseract,
        # а не автора документа; без пометки пользователь будет «исправлять»
        # то, чего в оригинале нет.
        if source_warning and isinstance(result, dict):
            result["_source_warning"] = source_warning
        return result

    task = await queue.submit("spellcheck", run)
    return {"task_id": task.id}
