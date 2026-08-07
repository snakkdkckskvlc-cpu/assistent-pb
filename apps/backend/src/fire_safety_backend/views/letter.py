"""Роутер: генерация официального письма и сборка DOCX по текущим полям."""

from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException

from .. import config
from ..infrastructure import secure_files
from ..infrastructure.generators.letter_docx import build_letter_docx, letterhead_requisites
from ..infrastructure.queue import queue
from ..models import LetterFields, LetterRequest
from ..pipelines import letter as pipelines
from ..services import ownership
from . import auth

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["letter"])


@router.post("/letter")
async def api_letter(req: LetterRequest, user: auth.User = Depends(auth.current_user)) -> dict:
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
                # Файл создан внутри задачи — владельца записываем здесь же,
                # иначе письмо с реквизитами компании смог бы скачать любой,
                # кому попалось его имя.
                await asyncio.to_thread(ownership.claim, filename, task.owner)
        return result

    task = await queue.submit("letter", run, owner=user.login)
    return {"task_id": task.id}


@router.get("/letter/letterhead")
async def api_letterhead(user: auth.User = Depends(auth.current_user)) -> dict:
    """Реквизиты для предпросмотра — из того самого бланка, что уйдёт адресату.

    Раньше предпросмотр держал их своей копией в letter.html. Копии расходятся,
    а расхождение здесь означает письмо, где на экране один расчётный счёт, а в
    документе другой.

    `missing: true` — шаблона нет. Сказать об этом надо ДО того, как человек
    напишет письмо: скачается оно тогда без реквизитов, и отправлять такое
    контрагенту нельзя.
    """
    lines = await asyncio.to_thread(letterhead_requisites)
    return {"lines": lines or [], "missing": lines is None}


@router.post("/letter/render")
async def api_letter_render(
    fields: LetterFields, user: auth.User = Depends(auth.current_user)
) -> dict:
    """Собирает DOCX на фирменном бланке из текущих полей письма — тех же,
    что вернула генерация, либо уже отредактированных в интерфейсе. Быстрая
    операция (нет LLM), поэтому вне очереди задач — сразу синхронный ответ."""
    filename = f"letter_{uuid.uuid4().hex[:12]}.docx"
    output_path = config.OUTPUT_DIR / filename
    try:
        # Письмо уходит на фирменном бланке с реквизитами и банковскими
        # данными — на диске оно лежит зашифрованным.
        with secure_files.encrypted_output(output_path) as writable:
            await asyncio.to_thread(build_letter_docx, fields.model_dump(), writable)
    except secure_files.StorageUnprotected as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось собрать DOCX: {e}") from e
    await asyncio.to_thread(ownership.claim, filename, user.login)
    # Без бланка письмо уходит контрагенту без реквизитов, ИНН и банковских
    # данных — то есть как обычный текст, а не официальный документ компании.
    # Файл при этом создаётся и открывается, поэтому сказать об этом надо
    # ЯВНО и до отправки: иначе разницу замечают, только сравнив письма
    # вручную (так и случилось, когда шаблон пропал).
    return {
        "docx_path": filename,
        "letterhead_missing": not config.LETTERHEAD_TEMPLATE.exists(),
    }
