"""Роутер: проверка документа на ошибки."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import config
from ..infrastructure import task_store
from ..infrastructure.generators.corrected_docx import build_corrected_docx
from ..infrastructure.queue import queue
from ..pipelines import spellcheck as pipelines
from ..services import ownership
from ..services.uploads import text_from_input_with_source
from . import auth

router = APIRouter(prefix="/api", tags=["spellcheck"])


class AcceptedFixes(BaseModel):
    """Какие правки человек принял: номера находок в списке errors."""

    task_id: str
    accepted: list[int] = Field(default_factory=list)


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


@router.post("/spellcheck/render")
async def api_spellcheck_render(
    body: AcceptedFixes, user: auth.User = Depends(auth.current_user)
) -> dict:
    """Пересобирает документ с ЧАСТЬЮ правок.

    Раньше исправленный DOCX приходил со всеми находками сразу: принять девять
    из двадцати трёх было нельзя, и человек либо соглашался со стилистикой
    модели целиком, либо правил документ руками заново.

    Пересборка идёт от исходного текста, а не откатом уже применённых замен:
    откат на неоднозначной правке промахивается, а расхождение показанного и
    скачанного в этом проекте уже стоило испорченного договора.
    """
    stored = await asyncio.to_thread(task_store.load, body.task_id, user.login)
    # Чужая или несуществующая — одинаково 404: 403 подтвердил бы, что задача
    # с таким id есть.
    if stored is None or not isinstance(stored.result, dict):
        raise HTTPException(status_code=404, detail="Task not found")

    result = stored.result
    errors = result.get("errors") or []
    source_text = result.get("_source_text")
    if source_text is None:
        # Задача посчитана до появления выборочных правок — исходника нет.
        raise HTTPException(
            status_code=409,
            detail="Эта проверка сделана раньше — выберите правки в новой проверке документа",
        )

    # Номера приходят с клиента: берём только те, что реально есть в списке,
    # и в исходном порядке — _apply_to_text применяет находки по смещениям и
    # обрабатывает их с конца документа, порядок на входе значения не имеет,
    # но дубли и мусор из запроса пропускать нельзя.
    picked = sorted({i for i in body.accepted if 0 <= i < len(errors)})
    chosen = [errors[i] for i in picked]

    stored_name = result.get("_source_path")
    source_path = config.UPLOAD_DIR / stored_name if stored_name else None

    corrected = pipelines.apply_selected(source_text, chosen)
    docx_path, edited_copy = await asyncio.to_thread(
        build_corrected_docx, corrected, chosen, source_path
    )
    await asyncio.to_thread(ownership.claim, docx_path.name, user.login)
    return {
        "docx_path": docx_path.name,
        "docx_is_copy": edited_copy,
        "applied": len(chosen),
        "corrected_text": corrected,
    }
