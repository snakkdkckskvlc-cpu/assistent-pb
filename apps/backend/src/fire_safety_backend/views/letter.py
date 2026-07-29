"""Роутер: генерация официального письма и сборка DOCX по текущим полям."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, HTTPException

from .. import config
from ..infrastructure.generators.letter_docx import build_letter_docx
from ..infrastructure.queue import queue
from ..models import LetterFields, LetterRequest
from ..pipelines import letter as pipelines

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["letter"])


@router.post("/letter")
async def api_letter(req: LetterRequest) -> dict:
    if not req.draft.strip():
        raise HTTPException(status_code=400, detail="Пустой набросок")

    async def run(task) -> dict:
        result = await pipelines.run_letter(req.draft, req.addressee_type, task=task)
        # Копия фирменного бланка собирается СРАЗУ, вместе с текстом письма, а
        # не после нажатия «Скачать». Пользователю нужен документ на бланке —
        # набор текстовых полей это ещё не письмо, и до кнопки было неочевидно,
        # что бланк вообще будет. Сборка идёт без обращения к модели и занимает
        # доли секунды; при правке полей в интерфейсе документ пересобирается
        # через /api/letter/render.
        if isinstance(result, dict):
            filename = f"letter_{uuid.uuid4().hex[:12]}.docx"
            try:
                await asyncio.to_thread(build_letter_docx, result, config.OUTPUT_DIR / filename)
            except Exception as e:  # noqa: BLE001 — текст письма ценен и без файла
                log.warning("Не удалось собрать бланк письма: %s", e)
            else:
                result["_docx_path"] = filename
        return result

    task = await queue.submit("letter", run)
    return {"task_id": task.id}


@router.post("/letter/render")
async def api_letter_render(fields: LetterFields) -> dict:
    """Собирает DOCX на фирменном бланке из текущих полей письма — тех же,
    что вернула генерация, либо уже отредактированных в интерфейсе. Быстрая
    операция (нет LLM), поэтому вне очереди задач — сразу синхронный ответ."""
    filename = f"letter_{uuid.uuid4().hex[:12]}.docx"
    output_path = config.OUTPUT_DIR / filename
    try:
        await asyncio.to_thread(build_letter_docx, fields.model_dump(), output_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось собрать DOCX: {e}") from e
    return {"docx_path": filename}
