"""Кнопка 3: набросок → официальное письмо."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .. import config
from ..infrastructure import llm
from ._prompts import load_prompt, make_token_counter

if TYPE_CHECKING:
    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)


async def run_letter(
    draft: str, addressee_type: str = "заказчик", task: Task | None = None
) -> dict:
    prompt = load_prompt("letter")

    # Подтягиваем подсказку тона из БД (справочник адресатов).
    # Если БД недоступна или адресат не найден — идём без подсказки.
    # SQLite — блокирующий вызов, уводим с event loop (там же крутится
    # весь остальной трафик приложения — очередь однопоточная).
    tone_hint = ""
    try:
        from ..services import addressees as addressee_service

        tone_hint = await asyncio.to_thread(addressee_service.get_tone_hint, addressee_type)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось получить tone_hint для '%s': %s", addressee_type, e)

    tone_line = f" (тон: {tone_hint})" if tone_hint else ""
    user_msg = (
        f"ТИП АДРЕСАТА: {addressee_type}{tone_line}\n\n"
        f"НАБРОСОК ПОЛЬЗОВАТЕЛЯ:\n---\n{draft}\n---\n\n"
        f"Составь официальное письмо."
    )
    if task:
        task.progress = "Формирую официальное письмо"
    result = await llm.chat_json(
        system=prompt,
        user=user_msg,
        num_predict=config.LLM_NUM_PREDICT_LETTER,
        on_delta=make_token_counter(task),
    )

    # Генерируем DOCX на основе фирменного бланка (python-docx — блокирующий
    # файловый I/O, тоже уводим с event loop).
    from ..infrastructure.generators.letter_docx import build_letter_docx

    output_path = config.OUTPUT_DIR / f"letter_{task.id if task else 'preview'}.docx"
    try:
        await asyncio.to_thread(build_letter_docx, result, output_path)
        result["_docx_path"] = str(output_path.name)
    except Exception as e:
        # build_letter_docx сама умеет обходиться без шаблона (fallback на
        # чистый DOCX) — сюда попадают только реальные сбои (битый .docx,
        # ошибка записи на диск и т.п.), не "шаблон не найден".
        log.warning("Не удалось собрать DOCX письма: %s", e, exc_info=True)
        result["_docx_path"] = None
        result["_warning"] = "Не удалось сформировать DOCX — доступен только текст письма"

    return result
