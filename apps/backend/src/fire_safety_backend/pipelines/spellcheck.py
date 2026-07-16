"""Кнопка 1: проверка орфографии/пунктуации/стиля."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fire_safety_rag import chunk_sentences

from .. import config
from ..infrastructure import languagetool, llm
from ._prompts import load_prompt

if TYPE_CHECKING:
    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)


async def run_spellcheck(text: str, task: Task | None = None) -> dict:
    prompt = load_prompt("spellcheck")

    # Первый проход — LanguageTool (детерминированный, без LLM): грамматика,
    # пунктуация, орфография по словарю (+ наш глоссарий терминов ПБ).
    # Ловит то, на чём LLM иногда либо тормозит, либо "исправляет" то, что
    # не было ошибкой. На весь документ разом — LT сам режет на предложения,
    # чанк-границы ему не нужны (см. tools/languagetool/, infrastructure/
    # languagetool.py, references/languagetool-master/README_reference.md).
    if task:
        task.progress = "Проверяю через LanguageTool"
    lt_errors = await languagetool.check(text)
    for e in lt_errors:
        e["chunk"] = 0

    # Sentence-aware чанкинг (см. packages/rag/src/fire_safety_rag/chunking.py) —
    # без overlap: правки не должны дублироваться между соседними чанками.
    chunks = chunk_sentences(text, config.SPELLCHECK_CHUNK_WORDS, overlap_words=0)
    all_errors: list[dict] = list(lt_errors)
    corrected_parts: list[str] = []

    for i, chunk in enumerate(chunks, start=1):
        if task:
            task.progress = f"Фрагмент {i}/{len(chunks)}"
        log.info("Spellcheck chunk %d/%d (%d words)", i, len(chunks), len(chunk.split()))
        result = await llm.chat_json(
            system=prompt,
            user=chunk,
            num_predict=config.LLM_NUM_PREDICT_SPELLCHECK,
        )
        errors = result.get("errors", []) or []
        # Модель иногда отступает от схемы (например, список строк вместо
        # списка объектов) — деградируем мягко вместо TypeError.
        if not isinstance(errors, list):
            log.warning("LLM вернула errors не списком (%s), игнорирую", type(errors).__name__)
            errors = []
        for e in errors:
            if isinstance(e, dict):
                e["chunk"] = i
                e["source"] = "llm"
        all_errors.extend(e for e in errors if isinstance(e, dict))
        corrected_parts.append(result.get("corrected_text", chunk))

    return {
        "errors": all_errors,
        "corrected_text": "\n\n".join(corrected_parts),
        "stats": {
            "total_errors": len(all_errors),
            "by_type": _count_by_type(all_errors),
            "chunks_processed": len(chunks),
        },
    }


def _count_by_type(errors: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in errors:
        t = e.get("type", "?")
        out[t] = out.get(t, 0) + 1
    return out
