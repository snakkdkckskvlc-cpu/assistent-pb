"""Кнопка 3: набросок → официальное письмо.

Генерация возвращает только текстовые поля (JSON от модели) — DOCX здесь
не строится. Интерфейс показывает поля РЕДАКТИРУЕМЫМИ, а DOCX собирается по
кнопке «Скачать» отдельным быстрым эндпоинтом (views/letter.py::
api_letter_render) уже из текущих значений, включая правки пользователя —
не из исходного ответа модели. Один источник правды для содержимого DOCX,
и не тратим время на файл, который тут же будет пересобран после правки.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fire_safety_rag import retrieve_letters

from .. import config
from ..infrastructure import llm
from ._prompts import load_prompt, make_progress_counter

if TYPE_CHECKING:
    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)

# Сколько символов одного письма-образца попадает в промпт: стиль и обороты
# видны и по началу письма, а весь контекст (4k токенов) съедать нельзя.
_EXAMPLE_MAX_CHARS = 1200


def _style_examples_block(draft: str) -> str:
    """2 реальных письма компании, ближайших к наброску, — образцы стиля.

    Коллекция letters_history наполняется вручную (scripts/index_letters.py);
    её нет — retrieve_letters отдаёт [] и генерация идёт без примеров.
    """
    try:
        examples = retrieve_letters(draft, top_k=2)
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось подтянуть примеры писем: %s", e)
        return ""
    if not examples:
        return ""
    parts = [
        "ПРИМЕРЫ РЕАЛЬНЫХ ПИСЕМ КОМПАНИИ — ориентируйся на их стиль, обороты и "
        "структуру. ЗАПРЕЩЕНО переносить из примеров факты: номера договоров, даты, "
        "суммы, ФИО, названия цехов и организаций. Нет нужного факта в наброске — "
        "ставь плейсхолдер в квадратных скобках, например «№[номер] от [дата]»:"
    ]
    for i, ex in enumerate(examples, start=1):
        text = ex.get("text", "")[:_EXAMPLE_MAX_CHARS]
        parts.append(f"--- Пример {i} ---\n{text}")
    return "\n\n".join(parts) + "\n\n"


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

    # Примеры стиля из архива реальных писем (RAG-коллекция letters_history).
    # Поиск по ChromaDB + эмбеддинг наброска — блокирующие, уводим с event loop.
    if task:
        task.progress = "Подбираю примеры из архива писем"
        task.percent = 5
    style_block = await asyncio.to_thread(_style_examples_block, draft)

    tone_line = f" (тон: {tone_hint})" if tone_hint else ""
    user_msg = (
        f"{style_block}"
        f"ТИП АДРЕСАТА: {addressee_type}{tone_line}\n\n"
        f"НАБРОСОК ПОЛЬЗОВАТЕЛЯ:\n---\n{draft}\n---\n\n"
        f"Составь официальное письмо."
    )
    if task:
        task.progress = "Формирую официальное письмо"
    return await llm.chat_json(
        system=prompt,
        user=user_msg,
        num_predict=config.LLM_NUM_PREDICT_LETTER,
        on_delta=make_progress_counter(
            task, config.LLM_NUM_PREDICT_LETTER, base_percent=10, span_percent=88
        ),
    )
