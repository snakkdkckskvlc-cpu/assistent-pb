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

# Границы структурных единиц нормативного акта — те же, что в чанкере RAG
# (packages/rag/.../chunking.py). Нужны здесь, чтобы обрезать фрагмент нормы
# по границе статьи, а не по счётчику символов.
_ARTICLE_BOUNDARY_RE = re.compile(
    r"^[ \t]*(?:Статья\s+\d+(?:\.\d+)*|Пункт\s+\d+(?:\.\d+)*|\d+\.\d+(?:\.\d+)*\.?\s+[А-ЯЁ])",
    re.MULTILINE,
)

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
# длины их режем. После перехода на постатейный чанкинг фрагмент — это одна
# статья (обычно 300–600 символов), а не простыня на 500 слов, поэтому за те же
# деньги влезает три нормы вместо одной. Раньше шесть чанков по 500 слов
# составляли ~11 000 токенов — больше всего окна 8k.
_RAG_CHUNKS_PER_PART = 3
_RAG_CHUNK_MAX_CHARS = 900

# Типы документов, релевантные для ДОГОВОРНЫХ рисков. После переиндексации
# корпус на 74% состоит из технических сводов правил (2463 чанка из 3334), и
# без фильтра запрос про неустойку выдавал СП про отопление и огнестойкость.
# Замер на реальном пункте о неустойке 2%/день: без фильтра — СП 7.13130,
# 69-ФЗ, СП 2.13130; с фильтром — ГК РФ ст. 715, 723, 708.
_CONTRACT_LAW_TYPES = ["code", "federal_law", "government_decree"]
# Признаки того, что пункт договора отсылает к пожарно-технической норме, а не
# к гражданскому праву, — тогда СП и ГОСТы как раз нужны.
_TECHNICAL_HINTS = (
    "пожарн",
    "сигнализац",
    "пожаротушен",
    "эвакуац",
    "огнестойк",
    "оповещен",
    "апс",
    "аупт",
    "соуэ",
    "гост",
    "сп ",
)


def _norm_filter_for(part: str) -> dict:
    """Какие типы документов искать под конкретную часть договора.

    Правило, а не LLM-роутер: роутер стоил бы ещё одного вызова модели (на CPU
    это 20–60 секунд на каждую из частей), а выбор здесь между двумя вариантами
    и делается по словам в тексте надёжнее и бесплатно.
    """
    low = part.lower()
    if any(h in low for h in _TECHNICAL_HINTS):
        # Пункт ссылается на пожарную технику — берём и нормы ПБ тоже.
        return {"status": {"$ne": "superseded"}}
    return {
        "$and": [
            {"status": {"$ne": "superseded"}},
            {"doc_type": {"$in": _CONTRACT_LAW_TYPES}},
        ]
    }


# Запас на разметку ролей, служебные токены и погрешность оценки.
_SAFETY_TOKENS = 300

# Потолок на финальный проход по договору. Ориентир: одна часть договора на
# этом железе считается 2.5–3 минуты, и финальный запрос заметно меньше их по
# объёму, так что 10 минут — это уже втрое больше ожидаемого.
_FINAL_PASS_TIMEOUT_SEC = 600


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


def _trim_norm_text(text: str, max_chars: int) -> str:
    """Обрезает фрагмент нормы по границе статьи, а не по счётчику символов.

    Посимвольная обрезка рубила статью на полуслове: замерено, что при лимите
    1800 из ст. 333 ГК РФ («Уменьшение неустойки») до модели доезжало 39% —
    обрыв приходился ровно на условие о снижении неустойки для
    предпринимателей. Лучше отдать на одну статью меньше, но целиком.
    """
    if len(text) <= max_chars:
        return text
    bounds = [m.start() for m in _ARTICLE_BOUNDARY_RE.finditer(text) if 0 < m.start() <= max_chars]
    if bounds:
        return text[: bounds[-1]].rstrip()
    # Разметку не опознали — режем хотя бы по границе предложения.
    cut = text.rfind(". ", 0, max_chars)
    return text[: cut + 1] if cut > max_chars // 2 else text[:max_chars]


def _extract_article_numbers(ref: str) -> list[str]:
    """Номера статей/пунктов из ссылки модели: «п. 2 ст. 401 ГК РФ» → ['401'].

    Берём именно номер статьи, а не номер пункта внутри неё: в контексте и в
    метаданных чанка единицей является статья.
    """
    if not ref:
        return []
    nums = re.findall(r"(?:ст\.?|статья|статьи)\s*(\d+(?:\.\d+)*)", ref, flags=re.IGNORECASE)
    nums += re.findall(r"(?:п\.?|пункт)\s*(\d+(?:\.\d+){1,})", ref, flags=re.IGNORECASE)
    return nums


def _verify_article_reference(ref: str, context_chunks: list[dict]) -> str:
    """Сверяет номер статьи из «ссылка_на_норму» с тем, что реально отдали модели.

    Тот же приём, что уже работает для цитат (_verify_quote) и для ID
    фрагментов (_resolve_chunk_id): не верить модели на слово, а проверить по
    выданному контексту. Замерено, зачем: для неустойки 2% в день модель
    сослалась на ст. 395 ГК РФ, тогда как верная — ст. 333, и ст. 395 в
    контекст вообще не попадала.

    Возвращает «подтверждена» | «не_в_контексте» | «не_проверялась».
    """
    numbers = _extract_article_numbers(ref)
    if not numbers:
        # «требует проверки юристом» и подобное — проверять нечего.
        return "не_проверялась"
    haystack = "\n".join(f"{c.get('article', '')}\n{c.get('text', '')}" for c in context_chunks)
    for num in numbers:
        if re.search(rf"(?:Статья|ст\.?)\s*{re.escape(num)}\b", haystack, flags=re.IGNORECASE):
            return "подтверждена"
        if re.search(rf"^{re.escape(num)}\b", haystack, flags=re.MULTILINE):
            return "подтверждена"
    return "не_в_контексте"


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


_CONTRACT_CLAUSE_RE = re.compile(r"^[ \t]*(\d+(?:\.\d+)*)\.?\s+([А-ЯЁ][^\n]{0,90})", re.MULTILINE)
_SANCTION_WORDS = ("неустойк", "штраф", "пеня", "пени", "убытк")
# Кого называют в пункте о санкции. Порядок важен: «Заказчик вправе взыскать с
# Подрядчика» — санкция против Подрядчика, хотя Заказчик упомянут первым,
# поэтому решает не порядок слов, а наличие обеих сторон и падеж.
_CONTRACTOR_WORDS = ("подрядчик", "исполнител")
_CUSTOMER_WORDS = ("заказчик",)


def _build_outline(text: str) -> list[str]:
    """Оглавление договора: номера и заголовки пунктов.

    Извлекается регэкспом ИЗ ПОЛНОГО текста, а не пересказывается моделью —
    значит, выдумать пункт, которого нет, невозможно.
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _CONTRACT_CLAUSE_RE.finditer(text):
        num, title = m.group(1), " ".join(m.group(2).split())
        if num in seen:
            continue
        seen.add(num)
        out.append(f"{num} {title}")
    return out


def _build_sanction_map(text: str) -> list[str]:
    """Все места договора, где упомянуты санкции, с номером пункта и стороной.

    Это прямой ответ на пропущенный при поразделном разборе системный вывод:
    «раздел Ответственность содержит санкции только против Подрядчика».
    Такое видно лишь по всему документу сразу, а каждая отдельная часть об
    этом умалчивает.
    """
    out: list[str] = []
    current = "?"
    for line in text.split("\n"):
        m = re.match(r"^[ \t]*(\d+(?:\.\d+)*)\.?\s", line)
        if m:
            current = m.group(1)
        low = line.lower()
        if not any(w in low for w in _SANCTION_WORDS):
            continue
        against = []
        if any(w in low for w in _CONTRACTOR_WORDS):
            against.append("Подрядчик")
        if any(w in low for w in _CUSTOMER_WORDS):
            against.append("Заказчик")
        who = "/".join(against) if against else "сторона не определена"
        out.append(f"п. {current} — упомянуты: {who} — {' '.join(line.split())[:110]}")
    return out


def _condense_findings(findings: list[dict], max_chars: int) -> str:
    lines: list[str] = []
    used = 0
    for f in findings:
        line = (
            f"[{f.get('критичность', '?')}] {str(f.get('в_чём_риск', ''))[:130]} "
            f"(норма: {f.get('ссылка_на_норму', '—')})"
        )
        if used + len(line) > max_chars:
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


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
            _norm_filter_for(part),
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
            f"[{cid}] {h.get('act_number') or h['source']}"
            f"{' · ' + h['article'] if h.get('article') else ''}\n"
            f"{_trim_norm_text(h['text'], _RAG_CHUNK_MAX_CHARS)}"
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
            # Номер статьи проверяем отдельно от ID фрагмента: модель может
            # сослаться на реальный фрагмент, но назвать при этом не ту статью.
            finding["_норма_статус"] = _verify_article_reference(
                str(finding.get("ссылка_на_норму", "")), top_chunks
            )
            finding["_часть"] = idx
        parts_findings.append([f for f in findings if isinstance(f, dict)])

        summary = result.get("сводка")
        if isinstance(summary, dict):
            summaries.append(summary)

    merged_findings = _merge_findings(parts_findings)
    summary = _merge_summaries(summaries)

    # Финальный проход по документу ЦЕЛИКОМ. Поразделный разбор в принципе не
    # способен увидеть свойства всего договора: главный вывод ручного эталона —
    # «раздел Ответственность содержит санкции только против Подрядчика, у
    # Заказчика их нет ни одной» — не выводится ни из одной отдельной части.
    # Полный текст в окно не влезает, поэтому на вход идёт не он, а извлечённые
    # РЕГЭКСПОМ оглавление и карта санкций (выдумать пункт по ним нельзя) плюс
    # сжатые находки.
    if len(parts) > 1 and merged_findings:
        if task:
            task.progress = "Свожу картину по договору целиком"
            task.percent = base_percent + int(span_percent * 0.95)
        try:
            # Жёсткий предел по времени. На живом прогоне этот вызов однажды
            # завис и держал задачу 5.7 часа сверх 40 минут основного разбора:
            # read-таймаут httpx считает паузу МЕЖДУ байтами, а в потоковом
            # режиме медленная генерация его не превышает никогда. Финальный
            # проход — приятное дополнение, а не обязательная часть, поэтому
            # лучше остаться без него, чем без результата вообще.
            final = await asyncio.wait_for(
                _final_pass(text, merged_findings, task), timeout=_FINAL_PASS_TIMEOUT_SEC
            )
        except TimeoutError:
            log.warning(
                "Финальный проход не уложился в %d сек — отдаём результат без системных выводов",
                _FINAL_PASS_TIMEOUT_SEC,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Финальный проход не удался: %s", e)
        else:
            if isinstance(final.get("сводка"), dict):
                summary = _merge_summaries([summary, final["сводка"]])
            conclusions = final.get("системные_выводы")
            if isinstance(conclusions, list) and conclusions:
                summary["системные_выводы"] = [str(c) for c in conclusions if str(c).strip()]

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
        "сводка": summary,
        "_rag_sources": sorted(rag_sources),
        "_частей": len(parts),
    }


async def _final_pass(text: str, findings: list[dict], task: Task | None) -> dict:
    """Один дешёвый вызов по всему договору: системные перекосы и сводка.

    Модели передаётся не текст договора (не влезет), а его скелет: оглавление и
    карта санкций, извлечённые регэкспом из ПОЛНОГО текста. Цитат не просим —
    они уже собраны и проверены на предыдущем шаге.
    """
    outline = _build_outline(text)
    sanctions = _build_sanction_map(text)
    user_msg = (
        "ОГЛАВЛЕНИЕ ДОГОВОРА:\n"
        + "\n".join(outline[:120])
        + "\n\nКАРТА САНКЦИЙ (кто упомянут в пунктах про неустойки/штрафы/убытки):\n"
        + "\n".join(sanctions[:60])
        + "\n\nНАЙДЕННЫЕ РИСКИ:\n"
        + _condense_findings(findings, 3000)
    )
    return await llm.chat_json(
        system=load_prompt("legal_summary"),
        user=user_msg,
        num_ctx=config.LLM_NUM_CTX_LEGAL,
        num_predict=config.LLM_NUM_PREDICT_LEGAL_PART,
        on_delta=make_progress_counter(task, config.LLM_NUM_PREDICT_LEGAL_PART, 95, 5),
    )
