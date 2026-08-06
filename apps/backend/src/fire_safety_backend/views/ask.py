"""Роутер: свободный вопрос по документу."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..infrastructure.queue import queue
from ..pipelines import ask as pipelines
from ..services.uploads import text_from_input_with_source
from . import auth

router = APIRouter(prefix="/api", tags=["ask"])


@router.post("/ask")
async def api_ask(
    question: str = Form(...),
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    user: auth.User = Depends(auth.current_user),
) -> dict:
    """Вопрос по документу: ответ только из файла, со ссылками на места в нём.

    Путь к исходному файлу нужен, чтобы для PDF взять НАСТОЯЩИЕ номера страниц:
    парсер отдаёт его текст постранично. Для остальных форматов страниц не
    существует, и ссылки даются на фрагменты — см. pipelines/ask.py.
    """
    if not question.strip():
        raise HTTPException(status_code=400, detail="Не задан вопрос")

    content, source_warning, source_path = await text_from_input_with_source(file, text)
    if not content.strip():
        raise HTTPException(status_code=400, detail="Пустой документ")

    async def run(task) -> dict:
        result = await pipelines.run_ask(question, content, task=task, source_path=source_path)
        # Скан распознан с ошибками — цитаты будут отличаться от оригинала, и
        # человек должен знать об этом до того, как начнёт их сверять.
        if source_warning and isinstance(result, dict):
            result["_source_warning"] = source_warning
        return result

    task = await queue.submit("ask", run, owner=user.login)
    return {"task_id": task.id}
