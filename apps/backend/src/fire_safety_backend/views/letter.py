"""Роутер: генерация официального письма и сборка DOCX по текущим полям."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException

from .. import config
from ..infrastructure.generators.letter_docx import build_letter_docx
from ..infrastructure.queue import queue
from ..models import LetterFields, LetterRequest
from ..pipelines import letter as pipelines

router = APIRouter(prefix="/api", tags=["letter"])


@router.post("/letter")
async def api_letter(req: LetterRequest) -> dict:
    if not req.draft.strip():
        raise HTTPException(status_code=400, detail="Пустой набросок")
    task = await queue.submit(
        "letter",
        lambda t: pipelines.run_letter(req.draft, req.addressee_type, task=t),
    )
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
    # Без бланка письмо уходит контрагенту без реквизитов, ИНН и банковских
    # данных — то есть как обычный текст, а не официальный документ компании.
    # Файл при этом создаётся и открывается, поэтому сказать об этом надо
    # ЯВНО и до отправки: иначе разницу замечают, только сравнив письма
    # вручную (так и случилось, когда шаблон пропал).
    return {
        "docx_path": filename,
        "letterhead_missing": not config.LETTERHEAD_TEMPLATE.exists(),
    }
