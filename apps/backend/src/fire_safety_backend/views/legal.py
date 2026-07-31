"""Роутер: юридический анализ договора."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..infrastructure.queue import queue
from ..pipelines import legal as pipelines
from ..services import text_from_input_with_warning
from . import auth

router = APIRouter(prefix="/api", tags=["legal"])


@router.post("/legal")
async def api_legal(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    user: auth.User = Depends(auth.current_user),
) -> dict:
    content, source_warning = await text_from_input_with_warning(file, text)
    if not content.strip():
        raise HTTPException(status_code=400, detail="Пустой текст договора")

    async def run(task) -> dict:
        result = await pipelines.run_legal_analysis(content, task=task)
        # Текст со скана мог приехать с ошибками распознавания — пользователь
        # должен это видеть рядом с находками, иначе непонятно, почему цитата
        # из договора не совпадает с бумагой дословно.
        if source_warning and isinstance(result, dict):
            result["_source_warning"] = source_warning
        return result

    task = await queue.submit("legal", run, owner=user.login)
    return {"task_id": task.id}
