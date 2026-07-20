"""Кнопка 2: юридический анализ договора."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import string
from typing import TYPE_CHECKING

from fire_safety_rag import retrieve_many

from .. import config
from ..infrastructure import llm
from ._prompts import load_prompt, make_progress_counter

if TYPE_CHECKING:
    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)

_SHORT_ID_LENGTH = 4


def generate_short_id(seed: str, length: int = _SHORT_ID_LENGTH) -> str:
    """Короткий детерминированный ID, посеянный содержимым чанка.

    Адаптировано из private-gpt (components/engines/citations/utils.py::
    generate_shorter_id, см. references/private-gpt-main/README_reference.md) —
    там это часть большого streaming-цитатного движка под llama-index; здесь
    оставлена только сама идея «короткий воспроизводимый ID из RNG,
    посеянного содержимым», без их DI/streaming-обвязки, не нужной при
    вызове через chat_json (готовый JSON, не поток токенов).
    """
    rng = random.Random(seed)
    return "".join(rng.choices(string.ascii_uppercase + string.digits, k=length))


def _resolve_chunk_id(source_id: str, chunk_ids: dict[str, dict]) -> dict | None:
    """Находит чанк по ID, устойчиво к тому, что модель иногда возвращает
    не голый ID, а весь тег целиком — например `[GGVR] GK_RF.txt` вместо
    `GGVR` (наблюдалось на живом ответе qwen2.5:7b). Сначала точное
    совпадение, затем — поиск ID-подстроки внутри возвращённого текста."""
    if not source_id:
        return None
    if source_id in chunk_ids:
        return chunk_ids[source_id]
    for cid, chunk in chunk_ids.items():
        if re.search(rf"\b{re.escape(cid)}\b", source_id):
            return chunk
    return None


def _verify_quote(quote: str, source_text: str) -> tuple[bool, int | None]:
    """Проверяет, что «цитата_из_договора» — реальная подстрока договора, и
    возвращает offset в ОРИГИНАЛЬНОМ тексте договора (не в схлопнутой по
    пробелам копии — там offset бесполезен для подсветки в реальном
    документе). Ищет гибким по пробелам regex, потому что модель иногда
    схлопывает/расставляет пробелы и переносы строк иначе при цитировании;
    re.escape на каждое слово по отдельности — цитаты из договоров обычно
    содержат точки и скобки («п. 4.2 (в редакции...)»), которые иначе сломали
    бы паттерн.

    Идея — grounding-паттерн OpenContracts (is_grounding_source, см.
    references/OpenContracts-main/README_reference.md): просить точную
    цитату и затем подтверждать её местоположение в исходном тексте, а не
    доверять модели на слово. У OpenContracts это отдельная модель/таблица;
    здесь — минимальная проверка без новых таблиц (см. также docs/08-references.md).
    """
    words = quote.split()
    if not words:
        return False, None
    pattern = r"\s+".join(re.escape(w) for w in words)
    match = re.search(pattern, source_text)
    return (match is not None), (match.start() if match else None)


def _assign_chunk_ids(chunks: list[dict]) -> dict[str, dict]:
    """Короткий ID на чанк для grounded-цитирования (см. generate_short_id).

    Раньше строился одной dict comprehension — при коллизии ID (два разных
    чанка получили одинаковый короткий ID, редко, но возможно на 36^4
    комбинациях) более ранний чанк молча пропадал из dict и, соответственно,
    из контекста, отданного модели. Здесь при коллизии детерминированно
    пере-сеиваем до уникальности — ни один чанк не теряется."""
    chunk_ids: dict[str, dict] = {}
    for i, h in enumerate(chunks):
        seed = f"{h['source']}|{i}"
        cid = generate_short_id(seed)
        suffix = 0
        while cid in chunk_ids:
            suffix += 1
            cid = generate_short_id(f"{seed}|{suffix}")
        chunk_ids[cid] = h
    return chunk_ids


async def run_legal_analysis(
    text: str,
    task: Task | None = None,
    base_percent: int = 0,
    span_percent: int = 100,
) -> dict:
    """base_percent/span_percent — доля общей полосы прогресса задачи, которую
    занимает именно этот вызов. По умолчанию — вся полоса (0..100), как при
    самостоятельном юр. анализе одного договора; pipelines/batch.py передаёт
    свою долю на файл, чтобы полоса не откатывалась назад между файлами
    пакета (иначе у каждого файла процент заново стартовал бы с нуля)."""
    prompt = load_prompt("legal")

    # Достаём релевантные нормы из RAG одним батч-запросом (retrieve_many),
    # а не пятью последовательными round-trip'ами в ChromaDB. Ключевые запросы:
    #  - общий по сути договора
    #  - штрафные санкции
    #  - ответственность
    if task:
        task.progress = "Подбираю нормы из базы"
        task.percent = base_percent + int(span_percent * 0.05)
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

    # Короткий ID на чанк — модель цитирует его в "источник_фрагмента",
    # а мы после ответа проверяем, что ID реально был среди отданных в
    # контекст, а не выдуман (grounded-цитирование, см. docstring
    # generate_short_id выше).
    chunk_ids = _assign_chunk_ids(top_chunks)
    context_block = "\n\n".join(
        f"[{cid}] {h['source']}\n{h['text']}" for cid, h in chunk_ids.items()
    )
    if not context_block:
        context_block = (
            "(нормативная база не подключена — сошлись на общие знания законодательства РФ)"
        )

    user_msg = (
        f"КОНТЕКСТ ИЗ НОРМАТИВНОЙ БАЗЫ (используй для ссылок на статьи; "
        f"у каждого фрагмента в квадратных скобках — его ID, укажи его в "
        f"поле источник_фрагмента, если опираешься на этот фрагмент):\n"
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
        on_delta=make_progress_counter(
            task,
            config.LLM_NUM_PREDICT_LEGAL,
            base_percent=base_percent + int(span_percent * 0.10),
            span_percent=int(span_percent * 0.85),
        ),
    )
    if not isinstance(result, dict):
        # Модель отступила от схемы и вернула не-объект (например, массив).
        log.warning("LLM вернула не-dict для юр. анализа (%s)", type(result).__name__)
        result = {"_raw": result}

    findings = result.get("находки")
    if isinstance(findings, list):
        for finding in findings:
            if not isinstance(finding, dict):
                continue

            # Grounded-цитирование: подтверждаем "источник_фрагмента" против
            # реально отданных в контекст чанков, а не доверяем модели.
            source_id = finding.get("источник_фрагмента")
            matched_chunk = _resolve_chunk_id(source_id, chunk_ids) if source_id else None
            finding["_источник_подтверждён"] = matched_chunk is not None
            if matched_chunk:
                finding["_источник_файл"] = matched_chunk["source"]

            # Quote-anchoring: подтверждаем, что "цитата_из_договора" —
            # реальная подстрока присланного текста, а не сочинённая модели.
            quote = finding.get("цитата_из_договора", "")
            verified, offset = _verify_quote(quote, text)
            finding["_цитата_найдена"] = verified
            if offset is not None:
                finding["_цитата_offset"] = offset

    result["_rag_sources"] = sorted({h["source"] for h in top_chunks})
    return result
