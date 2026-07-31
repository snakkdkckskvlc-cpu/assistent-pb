"""Роутер: проверка документа на ошибки."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from ..infrastructure.queue import queue
from ..pipelines import spellcheck as pipelines
from ..services.uploads import text_from_input_with_source
from . import auth

router = APIRouter(prefix="/api", tags=["spellcheck"])


@router.post("/spellcheck")
async def api_spellcheck(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    deep: bool = Form(default=True),
    user: auth.User = Depends(auth.current_user),
) -> dict:
    """deep=false — быстрая проверка только через LanguageTool (секунды вместо
    минут). Замер и обоснование — в docstring pipelines.spellcheck.run_spellcheck."""
    # Путь к исходному файлу нужен, чтобы отдать исправленный документ копией
    # оригинала — с сохранением форматирования, а не простынёй текста.
    content, source_warning, source_path = await text_from_input_with_source(file, text)
    if not content.strip():
        raise HTTPException(status_code=400, detail="Пустой текст")

    async def run(task) -> dict:
        result = await pipelines.run_spellcheck(
            content, task=task, source_path=source_path, deep=deep
        )
        # Орфография распознанного скана — это в основном ошибки Tesseract,
        # а не автора документа; без пометки пользователь будет «исправлять»
        # то, чего в оригинале нет.
        if source_warning and isinstance(result, dict):
            result["_source_warning"] = source_warning
        return result

    task = await queue.submit("spellcheck", run, owner=user.login)
    return {"task_id": task.id}
