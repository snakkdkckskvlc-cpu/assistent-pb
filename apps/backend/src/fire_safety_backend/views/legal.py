"""Роутер: юридический анализ договора."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .. import config
from ..infrastructure import secure_files, task_store
from ..infrastructure.generators.legal_docx import build_legal_docx
from ..infrastructure.generators.redline_docx import build_redline_docx
from ..infrastructure.queue import queue
from ..pipelines import legal as pipelines
from ..services import ownership
from ..services.uploads import text_from_input_with_source
from . import auth

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["legal"])


class AcceptedEdits(BaseModel):
    """Какие правки юрист принял: номера находок в списке `находки`."""

    task_id: str
    accepted: list[int] = Field(default_factory=list)


@router.post("/legal")
async def api_legal(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    user: auth.User = Depends(auth.current_user),
) -> dict:
    # Путь к исходному файлу нужен для правок в режиме рецензирования: они
    # вносятся в КОПИЮ оригинала, иначе у контрагента вместо договора окажется
    # пересобранная простыня без нумерации пунктов и приложений.
    content, source_warning, source_path = await text_from_input_with_source(file, text)
    if not content.strip():
        raise HTTPException(status_code=400, detail="Пустой текст договора")
    source_name = (file.filename or "") if file is not None else ""

    async def run(task) -> dict:
        result = await pipelines.run_legal_analysis(content, task=task)
        if not isinstance(result, dict):
            return result
        # Имя, а не путь: config.UPLOAD_DIR на боевой машине другой, а результат
        # задачи переживает перезапуск сервера (task_results).
        result["_source_path"] = source_path.name if source_path is not None else None
        result["_редлайн_возможен"] = (
            source_path is not None and source_path.suffix.lower() == ".docx"
        )
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


@router.post("/legal/redline")
async def api_legal_redline(
    body: AcceptedEdits, user: auth.User = Depends(auth.current_user)
) -> dict:
    """Договор с ВЫБРАННЫМИ правками в режиме рецензирования Word.

    Выборочность здесь обязательна, а не удобство: юрист соглашается не со
    всеми формулировками модели. Отдавать все правки разом — та же ошибка,
    которую уже исправляли в проверке орфографии, где нельзя было принять
    девять находок из двадцати трёх.

    Правки вносятся по требованию, а не вместе с разбором: редлайн нужен не
    каждому разобранному договору, а сборка правит копию файла целиком.
    """
    stored = await asyncio.to_thread(task_store.load, body.task_id, user.login)
    # Чужая или несуществующая — одинаково 404: 403 подтвердил бы, что задача
    # с таким id есть.
    if stored is None or not isinstance(stored.result, dict):
        raise HTTPException(status_code=404, detail="Task not found")

    result = stored.result
    findings = result.get("находки") or []
    stored_name = result.get("_source_path")
    if not stored_name:
        # Разбор текста, вставленного руками, или задача до появления редлайна.
        raise HTTPException(
            status_code=409,
            detail="Правки вносятся только в загруженный файл DOCX — разберите договор файлом",
        )

    source_path = config.UPLOAD_DIR / stored_name
    # Номера приходят с клиента: берём только существующие, мусор и дубли
    # отбрасываем.
    picked = sorted({i for i in body.accepted if 0 <= i < len(findings)})
    chosen = [findings[i] for i in picked]
    if not chosen:
        raise HTTPException(status_code=400, detail="Не выбрано ни одной правки")

    when = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        docx_path, applied, usable = await asyncio.to_thread(
            build_redline_docx, chosen, source_path, when
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e

    await asyncio.to_thread(ownership.claim, docx_path.name, user.login)
    return {
        "docx_path": docx_path.name,
        # Три разных числа, и путать их нельзя. Выбрано — сколько отметил
        # человек. Пригодно — сколько прошло проверку цитаты. Внесено —
        # сколько реально легло в документ. Разницу интерфейс обязан показать:
        # молчаливая деградация здесь означает, что юрист отправит контрагенту
        # договор, где половины согласованных правок нет.
        "выбрано": len(chosen),
        "пригодно": usable,
        "внесено": applied,
    }
