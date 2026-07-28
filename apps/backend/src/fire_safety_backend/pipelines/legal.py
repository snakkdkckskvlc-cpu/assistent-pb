"""Кнопка 2: юридический анализ договора."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import string
from typing import TYPE_CHECKING

from fire_safety_rag import chunk_sentences, retrieve_many

from .. import config
from ..infrastructure import llm
from ._prompts import load_prompt, make_progress_counter

if TYPE_CHECKING:
    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)

_SHORT_ID_LENGTH = 4

# Замерено на реальных договорах НЛМК (qwen2.5, русский текст): 2.57 символа
# на токен, 3.78 токена на слово. В расчёте бюджета берутся ЗАВЕДОМО ХУДШИЕ
# значения, а не замеренные: часть обязана влезть в окно гарантированно, а не
# «по средним показателям». Плотность токенов скачет от текста к тексту —
# таблицы, номера договоров, реквизиты и длинные числа дробятся на токены
# заметно мельче обычной прозы, и на таком куске средний коэффициент
# промахнулся бы в опасную сторону. Запас ~25% к символам и ~20% к словам.
# Реальный расход всё равно проверяется постфактум по prompt_eval_count
# (см. infrastructure/llm.py) — если оценка промахнётся, это будет видно
# в логе как ошибка, а не молча превратится в обрезанный контекст.
_CHARS_PER_TOKEN = 2.0
_TOKENS_PER_WORD = 4.6
# Сколько фрагментов нормативки даём модели на ОДНУ часть договора и до какой
# длины их режем. Чанк корпуса — до 500 слов (≈1900 токенов); шесть таких, как
# было раньше, — это ~11 000 токенов контекста ТОЛЬКО под нормы, что само по
# себе больше всего окна 8k.
_RAG_CHUNKS_PER_PART = 1
_RAG_CHUNK_MAX_CHARS = 1800
# Запас на разметку ролей, служебные токены и погрешность оценки.
_SAFETY_TOKENS = 300


def _estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def _contract_part_word_budget(prompt: str) -> int:
    """Сколько СЛОВ договора влезает в один запрос к модели.

    Считается от фактического окна, а не «на глаз»: окно минус место под
    ответ, минус системный промпт, минус зарезервированное под нормы. Если
    поднять LLM_NUM_CTX_LEGAL, частей автоматически станет меньше — формула
    подстроится сама.
    """
    return max(200, int(_input_budget_tokens(prompt) / _TOKENS_PER_WORD))


def _input_budget_tokens(prompt: str) -> int:
    """Сколько токенов доступно под ОДИН запрос, кроме самого текста договора."""
    budget = config.LLM_NUM_CTX_LEGAL - config.LLM_NUM_PREDICT_LEGAL_PART - _SAFETY_TOKENS
    budget -= _estimate_tokens(prompt)
    budget -= _RAG_CHUNKS_PER_PART * int(_RAG_CHUNK_MAX_CHARS / _CHARS_PER_TOKEN)
    return budget


def _split_oversized_parts(parts: list[str], prompt: str) -> list[str]:
    """Страховка поверх расчёта по словам: режем пополам всё, что по оценке
    в символах всё равно не влезает.

    Расчёт бюджета идёт в СЛОВАХ (chunk_sentences принимает max_words), а
    ограничение окна — в ТОКЕНАХ. Между ними коэффициент, который на плотном
    тексте (таблицы, номера, реквизиты) может оказаться хуже расчётного.
    Здесь проверяется уже готовый кусок в символах — то есть по другой,
    независимой оценке, и при расхождении он дробится дальше.
    """
    budget = _input_budget_tokens(prompt)
    out: list[str] = []
    queue = list(parts)
    while queue:
        part = queue.pop(0)
        words = part.split()
        # 60 слов — ниже этого дробить бессмысленно: смысл пункта договора
        # потеряется раньше, чем мы выиграем что-то по контексту.
        if _estimate_tokens(part) <= budget or len(words) <= 60:
            out.append(part)
            continue
        mid = len(words) // 2
        log.warning(
            "Юр. анализ: часть на %d слов не влезает в бюджет %d токенов — дроблю пополам",
            len(words),
            budget,
        )
        queue.insert(0, " ".join(words[mid:]))
        queue.insert(0, " ".join(words[:mid]))
    return out


def _normalize_quote(quote: str) -> str:
    return " ".join(str(quote).split()).casefold()


def _merge_findings(parts_findings: list[list[dict]]) -> list[dict]:
    """Склеивает находки со всех частей договора, убирая повторы.

    Одна и та же проблема может всплыть в двух соседних частях (например,
    пункт про неустойку упомянут и в разделе об ответственности, и в
    заключительных положениях) — показывать её дважды незачем.
    """
    merged: list[dict] = []
    seen: set[str] = set()
    for findings in parts_findings:
        for f in findings:
            if not isinstance(f, dict):
                continue
            key = _normalize_quote(f.get("цитата_из_договора", "")) or _normalize_quote(
                f.get("в_чём_риск", "")
            )
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(f)
    return merged


def _merge_summaries(summaries: list[dict]) -> dict:
    pros: list[str] = []
    cons: list[str] = []
    verdicts: list[str] = []
    for s in summaries:
        if not isinstance(s, dict):
            continue
        for key, bucket in (("плюсы_для_компании", pros), ("минусы_для_компании", cons)):
            value = s.get(key)
            if isinstance(value, list):
                bucket.extend(str(v) for v in value if str(v).strip())
        verdict = str(s.get("общий_вывод", "")).strip()
        if verdict:
            verdicts.append(verdict)

    def _dedup(items: list[str]) -> list[str]:
        out, seen = [], set()
        for i in items:
            k = i.casefold().strip()
            if k and k not in seen:
                seen.add(k)
                out.append(i)
        return out

    return {
        "плюсы_для_компании": _dedup(pros),
        "минусы_для_компании": _dedup(cons),
        "общий_вывод": " ".join(_dedup(verdicts)),
    }


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

    # Договор режется на части ПОД РАЗМЕР ОКНА модели. Раньше он уходил одним
    # куском: 17-страничный договор (~16 000 токенов) + нормы из RAG (~11 000)
    # при окне 8192 — Ollama молча отбрасывала лишнее С НАЧАЛА, вместе с
    # системным промптом. Модель получала хвост документа без единой инструкции
    # и возвращала не анализ рисков, а выписку реквизитов; проверено замером —
    # из 14 000 отправленных токенов обрабатывалось 4 098, и системный маркер
    # до модели не доезжал.
    part_words = _contract_part_word_budget(prompt)
    parts = await asyncio.to_thread(chunk_sentences, text, part_words, overlap_words=0)
    if not parts:
        parts = [text]
    parts = _split_oversized_parts(parts, prompt)
    log.info(
        "Юр. анализ: %d слов → %d част(и/ей) по ~%d слов (окно %d)",
        len(text.split()),
        len(parts),
        part_words,
        config.LLM_NUM_CTX_LEGAL,
    )

    parts_findings: list[list[dict]] = []
    summaries: list[dict] = []
    rag_sources: set[str] = set()

    for idx, part in enumerate(parts, start=1):
        part_base = base_percent + int(span_percent * (idx - 1) / len(parts))
        part_span = max(1, int(span_percent / len(parts)))

        if task:
            task.progress = f"Часть {idx}/{len(parts)}: подбираю нормы"
            task.percent = part_base

        # Нормы подбираются ПОД КАЖДУЮ ЧАСТЬ отдельно: для раздела про
        # ответственность нужны ст. 333/394 ГК РФ, для приёмки — другие. Раньше
        # выборка была общая на весь договор и к конкретному разделу подходила
        # хуже.
        hits = await asyncio.to_thread(
            retrieve_many,
            [part[:1500], "ответственность неустойка штраф убытки"],
            _RAG_CHUNKS_PER_PART,
        )
        context_chunks: list[dict] = []
        seen_keys: set[str] = set()
        for group in hits:
            for h in group:
                key = f"{h['source']}|{h['text'][:50]}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    context_chunks.append(h)
        context_chunks.sort(key=lambda h: h.get("score", 0), reverse=True)
        top_chunks = context_chunks[:_RAG_CHUNKS_PER_PART]
        rag_sources.update(h["source"] for h in top_chunks)

        # Короткий ID на чанк — модель цитирует его в "источник_фрагмента",
        # а мы после ответа проверяем, что ID реально был среди отданных в
        # контекст, а не выдуман (grounded-цитирование, см. docstring
        # generate_short_id выше).
        chunk_ids = _assign_chunk_ids(top_chunks)
        context_block = "\n\n".join(
            f"[{cid}] {h['source']}\n{h['text'][:_RAG_CHUNK_MAX_CHARS]}"
            for cid, h in chunk_ids.items()
        )
        if not context_block:
            context_block = (
                "(нормативная база не подключена — сошлись на общие знания законодательства РФ)"
            )

        part_note = (
            f" (часть {idx} из {len(parts)}; анализируй только присланный фрагмент)"
            if len(parts) > 1
            else ""
        )
        user_msg = (
            f"КОНТЕКСТ ИЗ НОРМАТИВНОЙ БАЗЫ (используй для ссылок на статьи; "
            f"у каждого фрагмента в квадратных скобках — его ID, укажи его в "
            f"поле источник_фрагмента, если опираешься на этот фрагмент):\n"
            f"---\n{context_block}\n---\n\n"
            f"ДОГОВОР ДЛЯ АНАЛИЗА{part_note}:\n---\n{part}\n---"
        )

        if task:
            task.progress = (
                f"Часть {idx}/{len(parts)}: модель анализирует договор"
                if len(parts) > 1
                else "Модель анализирует договор (может занять несколько минут)"
            )

        sent_tokens = _estimate_tokens(prompt) + _estimate_tokens(user_msg)
        if sent_tokens > config.LLM_NUM_CTX_LEGAL - config.LLM_NUM_PREDICT_LEGAL_PART:
            # Страховка: если оценка всё же промахнулась, это надо увидеть в
            # логе, а не молча получить обрезанный контекст, как было раньше.
            log.warning(
                "Юр. анализ, часть %d: ~%d токенов при окне %d — возможна обрезка контекста",
                idx,
                sent_tokens,
                config.LLM_NUM_CTX_LEGAL,
            )

        result = await llm.chat_json(
            system=prompt,
            user=user_msg,
            num_ctx=config.LLM_NUM_CTX_LEGAL,
            num_predict=config.LLM_NUM_PREDICT_LEGAL_PART,
            on_delta=make_progress_counter(
                task,
                config.LLM_NUM_PREDICT_LEGAL_PART,
                base_percent=part_base,
                span_percent=part_span,
            ),
        )
        if not isinstance(result, dict):
            log.warning("LLM вернула не-dict для части %d (%s)", idx, type(result).__name__)
            continue

        findings = result.get("находки")
        if not isinstance(findings, list):
            findings = []
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            source_id = finding.get("источник_фрагмента")
            matched_chunk = _resolve_chunk_id(source_id, chunk_ids) if source_id else None
            finding["_источник_подтверждён"] = matched_chunk is not None
            if matched_chunk:
                finding["_источник_файл"] = matched_chunk["source"]
            finding["_часть"] = idx
        parts_findings.append([f for f in findings if isinstance(f, dict)])

        summary = result.get("сводка")
        if isinstance(summary, dict):
            summaries.append(summary)

    merged_findings = _merge_findings(parts_findings)

    # Quote-anchoring: цитата проверяется по ПОЛНОМУ тексту договора, а не по
    # той части, из которой пришла, — так offset остаётся валидным для всего
    # документа.
    for finding in merged_findings:
        quote = finding.get("цитата_из_договора", "")
        verified, offset = _verify_quote(quote, text)
        finding["_цитата_найдена"] = verified
        if offset is not None:
            finding["_цитата_offset"] = offset

    return {
        "находки": merged_findings,
        "сводка": _merge_summaries(summaries),
        "_rag_sources": sorted(rag_sources),
        "_частей": len(parts),
    }
