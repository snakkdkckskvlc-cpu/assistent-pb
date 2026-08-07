"""Роутер: юридический анализ договора."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .. import config
from ..infrastructure import secure_files
from ..infrastructure.generators.legal_docx import build_legal_docx
from ..infrastructure.queue import queue
from ..pipelines import legal as pipelines
from ..services import ownership, text_from_input_with_warning
from . import auth

log = logging.getLogger(__name__)

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
    source_name = (file.filename or "") if file is not None else ""

    async def run(task) -> dict:
        result = await pipelines.run_legal_analysis(content, task=task)
        if not isinstance(result, dict):
            return result
        # Текст со скана мог приехать с ошибками распознавания — пользователь
        # должен это видеть рядом с находками, иначе непонятно, почему цитата
        # из договора не совпадает с бумагой дословно.
        if source_warning:
            result["_source_warning"] = source_warning

        # Разбор собирается в DOCX сразу, как письмо: у проверки орфографии
        # выгрузка была, у пакетной проверки — сводный отчёт, а одиночный
        # разбор приходилось копировать руками. Именно его несут на переговоры.
        output_path = config.OUTPUT_DIR / f"legal_{task.id}.docx"
        try:
            # Разбор содержит цитаты из договора целиком — на диск он должен
            # попасть уже зашифрованным.
            with secure_files.encrypted_output(output_path) as writable:
                await asyncio.to_thread(build_legal_docx, result, writable, source_name=source_name)
        except Exception as e:  # noqa: BLE001 — находки ценны и без файла
            log.warning("Не удалось собрать DOCX разбора: %s", e, exc_info=True)
        else:
            result["_docx_path"] = output_path.name
            # Владельца записываем здесь же: файл создан внутри задачи, и без
            # этого разбор чужого договора скачал бы любой, кому попалось имя.
            if task.owner:
                await asyncio.to_thread(ownership.claim, output_path.name, task.owner)
        return result

    task = await queue.submit("legal", run, owner=user.login)
    return {"task_id": task.id}
