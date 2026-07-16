"""Пайплайны трёх кнопок. Каждый — асинхронная функция, возвращающая dict."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from fire_safety_rag import retrieve_many

from .. import config
from ..infrastructure import llm

if TYPE_CHECKING:
    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)


def _load_prompt(name: str) -> str:
    return (config.PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _chunk_by_words(text: str, chunk_words: int) -> list[str]:
    words = text.split()
    if len(words) <= chunk_words:
        return [text]
    chunks: list[str] = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i : i + chunk_words]))
        i += chunk_words
    return chunks


# --- Кнопка 1: проверка орфографии/пунктуации/стиля ---


async def run_spellcheck(text: str, task: Task | None = None) -> dict:
    prompt = _load_prompt("spellcheck")
    chunks = _chunk_by_words(text, config.SPELLCHECK_CHUNK_WORDS)
    all_errors: list[dict] = []
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


# --- Кнопка 2: юридический анализ договора ---


async def run_legal_analysis(text: str, task: Task | None = None) -> dict:
    prompt = _load_prompt("legal")

    # Достаём релевантные нормы из RAG одним батч-запросом (retrieve_many),
    # а не пятью последовательными round-trip'ами в ChromaDB. Ключевые запросы:
    #  - общий по сути договора
    #  - штрафные санкции
    #  - ответственность
    if task:
        task.progress = "Подбираю нормы из базы"
    rag_queries = [
        text[:1500],
        "ответственность сторон штрафные санкции неустойка",
        "порядок сдачи-приёмки работ услуг",
        "форс-мажор обстоятельства непреодолимой силы",
        "требования пожарной безопасности проектирование монтаж систем",
    ]
    context_chunks: list[dict] = []
    seen_keys: set[str] = set()
    hits_per_query = await asyncio.to_thread(retrieve_many, rag_queries, 2)
    for hits in hits_per_query:
        for h in hits:
            key = f"{h['source']}|{h['text'][:50]}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            context_chunks.append(h)

    # Ранжируем по score и берём максимум 6 чанков — иначе не влезает в 8k контекст 7B-модели
    context_chunks.sort(key=lambda h: h.get("score", 0), reverse=True)
    top_chunks = context_chunks[:6]
    context_block = "\n\n".join(f"[{h['source']}]\n{h['text']}" for h in top_chunks)
    if not context_block:
        context_block = (
            "(нормативная база не подключена — сошлись на общие знания законодательства РФ)"
        )

    user_msg = (
        f"КОНТЕКСТ ИЗ НОРМАТИВНОЙ БАЗЫ (используй для ссылок на статьи):\n"
        f"---\n{context_block}\n---\n\n"
        f"ДОГОВОР ДЛЯ АНАЛИЗА:\n---\n{text}\n---"
    )

    if task:
        task.progress = "Модель анализирует договор (может занять несколько минут)"

    result = await llm.chat_json(
        system=prompt,
        user=user_msg,
        num_ctx=8192,
        num_predict=config.LLM_NUM_PREDICT_LEGAL,
    )
    if not isinstance(result, dict):
        # Модель отступила от схемы и вернула не-объект (например, массив).
        log.warning("LLM вернула не-dict для юр. анализа (%s)", type(result).__name__)
        result = {"_raw": result}
    result["_rag_sources"] = sorted({h["source"] for h in top_chunks})
    return result


# --- Кнопка 3: набросок → официальное письмо ---


async def run_letter(
    draft: str, addressee_type: str = "заказчик", task: Task | None = None
) -> dict:
    prompt = _load_prompt("letter")

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
