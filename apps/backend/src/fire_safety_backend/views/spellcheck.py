"""Роутер: проверка документа на ошибки."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import config
from ..infrastructure.queue import queue
from ..pipelines import spellcheck as pipelines
from ..services import text_from_input_with_source
from ..services.docx_edit import apply_corrections_to_docx

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["spellcheck"])


def _corrected_docx(source: Path, errors: list[dict]) -> tuple[str, dict] | None:
    """Копия исходного DOCX с применёнными правками.

    Возвращает (имя файла, отчёт) либо None, если применять нечего или
    исходник не DOCX. Отдельная функция, чтобы её было видно в логах и чтобы
    падение правки не уносило с собой результат самой проверки.
    """
    corrections = [
        {"before": e.get("before", ""), "after": e.get("after", "")}
        for e in errors
        if isinstance(e, dict) and e.get("before") and e.get("after")
    ]
    if not corrections:
        return None
    out_name = f"{source.stem}_ispravleno_{uuid.uuid4().hex[:8]}.docx"
    report = apply_corrections_to_docx(source, config.OUTPUT_DIR / out_name, corrections)
    return out_name, report.as_dict()


@router.post("/spellcheck")
async def api_spellcheck(
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
) -> dict:
    content, source_warning, source_path = await text_from_input_with_source(file, text)
    if not content.strip():
        raise HTTPException(status_code=400, detail="Пустой текст")

    async def run(task) -> dict:
        result = await pipelines.run_spellcheck(content, task=task)
        # Орфография распознанного скана — это в основном ошибки Tesseract,
        # а не автора документа; без пометки пользователь будет «исправлять»
        # то, чего в оригинале нет.
        if source_warning and isinstance(result, dict):
            result["_source_warning"] = source_warning

        # Главное, ради чего всё затевалось: правки применяются к КОПИИ
        # присланного документа, а не отдаются простынёй текста. Шрифты,
        # отступы, таблицы и колонтитулы остаются на месте — меняются только
        # символы с ошибками.
        if isinstance(result, dict) and source_path and source_path.suffix.lower() == ".docx":
            try:
                built = await asyncio.to_thread(
                    _corrected_docx, source_path, result.get("errors", [])
                )
            except Exception as e:  # noqa: BLE001 — список ошибок ценен и без файла
                log.warning("Не удалось собрать исправленный DOCX: %s", e)
            else:
                if built:
                    result["_docx_path"], result["_docx_отчёт"] = built
        return result

    task = await queue.submit("spellcheck", run)
    return {"task_id": task.id}
