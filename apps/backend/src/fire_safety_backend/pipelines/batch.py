"""Кнопка 4: пакетная проверка договоров.

Несколько файлов одной задачей: каждый файл распознаётся (DOCX/PDF/OCR),
классифицируется (services/classify.py), и только договоры уходят в
юридический анализ — гонять письмо или смету через LLM по несколько минут
бессмысленно, такие файлы помечаются типом и пропускаются. В конце — сводный
DOCX-отчёт по всему пакету.

Очередь остаётся однопоточной (на CPU LLM и так занимает все ядра —
параллелить файлы некуда), файлы идут последовательно, прогресс и счётчик
токенов у задачи общие на весь пакет.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .. import config
from ..infrastructure.parsers import extract_text
from ..services.classify import classify_document
from .legal import run_legal_analysis

if TYPE_CHECKING:
    from pathlib import Path

    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)


async def run_batch(file_paths: list[Path], task: Task | None = None) -> dict:
    items: list[dict] = []
    contracts = 0

    for i, path in enumerate(file_paths, start=1):
        if task:
            task.progress = f"Файл {i}/{len(file_paths)}: {path.name}"
        item: dict = {"файл": path.name}

        try:
            text = await asyncio.to_thread(extract_text, path)
        except Exception as e:
            log.warning("Батч: не удалось прочитать %s: %s", path.name, e)
            item.update({"тип": "не распознан", "пропущен": True, "причина": str(e)})
            items.append(item)
            continue

        if not text.strip():
            item.update({"тип": "пустой", "пропущен": True, "причина": "Пустой текст"})
            items.append(item)
            continue

        cls = await asyncio.to_thread(classify_document, text)
        item["тип"] = cls["type"]

        if cls["type"] != "договор":
            item.update(
                {
                    "пропущен": True,
                    "причина": f"Не договор (распознан тип «{cls['type']}») — "
                    f"юр. анализ не запускался",
                }
            )
            items.append(item)
            continue

        contracts += 1
        analysis = await run_legal_analysis(text, task=task)
        findings = analysis.get("находки")
        item["находки"] = findings if isinstance(findings, list) else []
        item["сводка"] = analysis.get("сводка")
        item["пропущен"] = False
        items.append(item)

    result: dict = {
        "файлы": items,
        "stats": {
            "всего": len(file_paths),
            "договоров": contracts,
            "пропущено": len(file_paths) - contracts,
        },
    }

    # Сводный DOCX-отчёт (python-docx — блокирующий I/O, уводим с event loop).
    from ..infrastructure.generators.batch_docx import build_batch_docx

    output_path = config.OUTPUT_DIR / f"batch_{task.id if task else 'preview'}.docx"
    try:
        await asyncio.to_thread(build_batch_docx, items, output_path)
        result["_docx_path"] = str(output_path.name)
    except Exception as e:
        log.warning("Не удалось собрать сводный DOCX батча: %s", e, exc_info=True)
        result["_docx_path"] = None

    return result
