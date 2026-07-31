"""Кнопка 4: пакетная проверка договоров.

Несколько файлов одной задачей: каждый файл распознаётся (DOCX/PDF/OCR),
классифицируется (services/classify.py), и только договоры уходят в
юридический анализ — гонять письмо или смету через LLM по несколько минут
бессмысленно, такие файлы помечаются типом и пропускаются. В конце — сводный
DOCX-отчёт по всему пакету.

Очередь остаётся однопоточной (на CPU LLM и так занимает все ядра —
параллелить файлы некуда), файлы идут последовательно. Полоса прогресса
общая на весь пакет: каждому файлу достаётся своя доля (100/N процентов),
юр. анализ договора получает её через base_percent/span_percent — иначе
полоса откатывалась бы назад на старте анализа каждого следующего файла.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from .. import config
from ..infrastructure import secure_files
from ..infrastructure.parsers import extract_text_with_meta
from ..services import ownership
from ..services.classify import classify_document
from .legal import run_legal_analysis

if TYPE_CHECKING:
    from pathlib import Path

    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)


async def run_batch(file_paths: list[Path], task: Task | None = None) -> dict:
    items: list[dict] = []
    contracts = 0

    file_span = max(1, int(100 / len(file_paths)))
    for i, path in enumerate(file_paths, start=1):
        file_base = int(100 * (i - 1) / len(file_paths))
        if task:
            task.progress = f"Файл {i}/{len(file_paths)}: {path.name}"
            task.percent = file_base
        item: dict = {"файл": path.name}

        try:
            # По расшифрованной копии: парсеры и OCR умеют только настоящий
            # файл на диске. Копия исчезает сразу после разбора файла, а не
            # живёт до конца пакета.
            with secure_files.plaintext(path) as readable:
                text, extraction = await asyncio.to_thread(extract_text_with_meta, readable)
            if extraction.warning:
                item["предупреждение"] = extraction.warning
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
        analysis = await run_legal_analysis(
            text, task=task, base_percent=file_base, span_percent=file_span
        )
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
        # Отчёт содержит находки по всем договорам пакета — на диск он должен
        # попасть уже зашифрованным.
        with secure_files.encrypted_output(output_path) as writable:
            await asyncio.to_thread(build_batch_docx, items, writable)
        result["_docx_path"] = str(output_path.name)
        if task is not None and task.owner:
            await asyncio.to_thread(ownership.claim, output_path.name, task.owner)
    except Exception as e:
        log.warning("Не удалось собрать сводный DOCX батча: %s", e, exc_info=True)
        result["_docx_path"] = None

    return result
