"""Кнопка 1: проверка орфографии и пунктуации."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING

from fire_safety_rag import chunk_sentences

from .. import config
from ..infrastructure import languagetool, llm
from ..infrastructure.generators.corrected_docx import build_corrected_docx
from ..services import ownership
from ._prompts import load_prompt, make_progress_counter

if TYPE_CHECKING:
    from pathlib import Path

    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)

_GLOSSARY_PATH = config.PROJECT_DIR / "tools" / "languagetool" / "dict" / "spelling_global.txt"


def _load_glossary_terms() -> list[str]:
    """Единый источник фирменных терминов — тот же файл, что LanguageTool
    подключает через classpath (tools/languagetool/start.sh). Раньше список
    дублировался прозой прямо в resources/prompts/spellcheck.txt и молча
    расходился со словарём LT."""
    if not _GLOSSARY_PATH.exists():
        return []
    terms = []
    for line in _GLOSSARY_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        terms.append(line)
    return terms


def _with_known_errors(chunk: str, lt_errors: list[dict]) -> str:
    """Показывает модели то, что LanguageTool уже нашёл в этом же фрагменте.

    Раньше LanguageTool отрабатывал ДО модели, но результат использовался
    только для дедупликации ПОСЛЕ — модель искала вслепую и тратила выдачу на
    те же очевидные опечатки. Здесь она видит их сразу и может заняться тем,
    чего правилами не поймать: вводными оборотами, обособлением, «что бы»
    против «чтобы» — на замере это ровно те девять ошибок из двадцати девяти,
    которые нашла только она.
    """
    known = [
        str(e.get("before", "")).strip()
        for e in lt_errors
        if str(e.get("before", "")).strip() and str(e.get("before", "")).strip() in chunk
    ]
    if not known:
        return chunk
    listed = "; ".join(dict.fromkeys(known))
    # Мягкой формулировки («их повторять не нужно») модели не хватало. Замерено
    # на размеченном наборе: во фрагменте с опечаткой она называла ровно эту
    # опечатку и останавливалась, пропуская обращение и вводное слово в том же
    # предложении. Поэтому здесь не просьба, а прямое переназначение задачи:
    # орфография в этом фрагменте закрыта, ищи пунктуацию.
    return (
        f"{chunk}\n\n"
        f"ОРФОГРАФИЯ В ЭТОМ ФРАГМЕНТЕ УЖЕ ПРОВЕРЕНА словарём, найдено: {listed}.\n"
        f"Эти слова в ответ НЕ включай — они уже исправлены без тебя.\n"
        f"Твоя задача здесь — ПУНКТУАЦИЯ: запятые при обращении, вводных словах, "
        f"деепричастных и причастных оборотах, между однородными членами, перед "
        f"союзами (а, но, однако, что, чтобы, который); тире между подлежащим и "
        f"сказуемым. Проверь предложение целиком, а не только начало."
    )


def _apply_to_text(text: str, errors: list[dict]) -> str:
    """Собирает исправленный текст из найденных правок.

    Нужно в быстром режиме: модель не вызывается, а показать результат целиком
    всё равно надо. Замены идут в порядке убывания длины «было» — короткий
    фрагмент может оказаться частью длинного, и заменив его первым, мы
    разрушили бы длинный.
    """
    out = text
    for e in sorted(errors, key=lambda x: len(str(x.get("before", ""))), reverse=True):
        before, after = str(e.get("before", "")), str(e.get("after", ""))
        if before and after and before != after and before not in after:
            out = out.replace(before, after)
    return out


def _normalize_before(text: str) -> str:
    return " ".join(text.split()).casefold()


# Короче этого цитату не принимаем: правки применяются глобальной заменой, и
# однобуквенное «и» переписало бы весь документ.
_MIN_QUOTE_CHARS = 4


def _anchor_to_source(before: str, text: str) -> str | None:
    """Точная подстрока исходного текста, которую имела в виду модель. None —
    такого места в документе нет.

    Зачем. Правка применяется к файлу как `text.replace(before, after)`, то
    есть `before` обязан встречаться в исходнике дословно. Модель это правило
    нарушает предсказуемым образом: цитирует фрагмент УЖЕ С ИСПРАВЛЕНИЕМ.

    Замерено на размеченном наборе: тире между подлежащим и сказуемым модель
    находила верно, но присылала «Наша компания — надёжный партнёр», хотя в
    документе написано «Наша компания надёжный партнёр». Такая правка молча не
    применялась — в списке она есть, в документе её нет. Худший вид отказа:
    человек считает, что ошибка исправлена.

    Поэтому цитата ищется по последовательности слов, а знаки препинания между
    ними считаются любыми: так находится настоящий фрагмент документа, и
    правка становится применимой.
    """
    if not before or not text:
        return None
    # Слишком короткая цитата опасна независимо от того, есть она в тексте или
    # нет: правки применяются глобальной заменой (_apply_to_text), и «и» или
    # «в» переписали бы весь документ.
    if len(before.strip()) < _MIN_QUOTE_CHARS:
        return None
    if before in text:
        return before
    words = re.findall(r"\w+", before, flags=re.UNICODE)
    # По одному слову привязываться на глаз нельзя: короткое слово найдётся где
    # угодно и подменит правку случайным местом документа.
    if len(words) < 2:
        return None
    pattern = r"[^\w]*".join(re.escape(w) for w in words)
    match = re.search(pattern, text, flags=re.IGNORECASE | re.UNICODE)
    return match.group(0) if match else None


def _keep_applicable(errors: list[dict], text: str) -> list[dict]:
    """Оставляет только правки, которые реально применятся к документу.

    Отбрасываются две породы находок модели: пустышки (`before` совпадает с
    `after` — «исправление», ничего не меняющее) и выдуманные цитаты, которых
    в документе нет. И то и другое выглядит в списке как работа, а документ не
    меняет.
    """
    kept: list[dict] = []
    for e in errors:
        if e.get("source") == "languagetool":
            kept.append(e)
            continue
        before, after = str(e.get("before", "")), str(e.get("after", ""))
        anchored = _anchor_to_source(before, text)
        if anchored is None:
            log.info("Правка модели не найдена в документе, отбрасываю: %r", before[:80])
            continue
        if _normalize_before(anchored) == _normalize_before(after):
            log.info("Правка модели ничего не меняет, отбрасываю: %r", before[:80])
            continue
        kept.append({**e, "before": anchored})
    return kept


def _changed_tokens(error: dict) -> frozenset[str]:
    """Слова, которые правка добавляет или меняет, — суть правки, а не её цитата.

    Знаки препинания намеренно остаются приклеенными к слову: правка
    «монтаж наладку» → «монтаж, наладку» меняет токен «монтаж» на «монтаж,», и
    только так пунктуационная правка вообще видна на уровне слов.
    """
    before = set(_normalize_before(error.get("before", "")).split())
    after = set(_normalize_before(error.get("after", "")).split())
    return frozenset(after - before)


def _dedup_errors(errors: list[dict]) -> list[dict]:
    """LT и LLM иногда репортят одну и ту же ошибку — LT детерминирован,
    при конфликте оставляем его и отбрасываем совпавший LLM-дубликат.

    ### Почему сравниваются ПРАВКИ, а не цитаты

    Раньше дубликатом считалось пересечение подстрок в любую сторону. Модель
    же цитирует не слово, а всё предложение, — и находка отбрасывалась, если
    ГДЕ-НИБУДЬ внутри этого предложения LanguageTool нашёл свою ошибку.

    Замерено на размеченном наборе (scripts/evaluate_spellcheck.py): в письме
    01 так молча уничтожались три верные правки подряд — причастный оборот,
    «в течении» → «в течение» и «что бы» → «чтобы». Каждая пропала только
    потому, что в том же предложении LT нашёл опечатку. По итогам модель не
    добавляла к LT ничего, и это выглядело как «модель слабая».

    Теперь дубликат — это правка, которая меняет ТО ЖЕ, что уже нашёл LT (или
    его подмножество). Если модель правит сверх того — обособляет оборот, а не
    только исправляет слово, — находка остаётся.
    """
    lt_errors = [e for e in errors if e.get("source") == "languagetool"]
    other_errors = [e for e in errors if e.get("source") != "languagetool"]
    lt_changes = [c for c in (_changed_tokens(e) for e in lt_errors) if c]

    deduped = list(lt_errors)
    for e in other_errors:
        change = _changed_tokens(e)
        is_dup = bool(change) and any(change <= lt_change for lt_change in lt_changes)
        if not is_dup:
            deduped.append(e)
    return deduped


async def run_spellcheck(
    text: str,
    task: Task | None = None,
    source_path: Path | None = None,
    deep: bool = True,
) -> dict:
    """deep=False — только LanguageTool, без обращения к модели.

    Замер воспроизводимый, набор лежит в репозитории:

        python scripts/evaluate_spellcheck.py          # полный режим
        python scripts/evaluate_spellcheck.py --fast   # только LanguageTool

    22 намеренно заложенные ошибки в двух деловых письмах
    (apps/backend/tests/fixtures/spellcheck/), qwen2.5:7b-instruct:

        LanguageTool    8/22 (36%)     1,2 с
        вместе         19/22 (86%)   490 с

    Ловят они РАЗНОЕ, и это главная причина держать оба прохода: LanguageTool
    закрывает орфографию по словарю целиком (8/8) и почти не видит пунктуацию;
    модель — наоборот, берёт контекстное обособление, которое правилами не
    поймать. Поэтому быстрый режим не заменяет глубокий, а даёт мгновенный
    результат там, где ждать восемь минут незачем.

    До правок дедупликации, промпта и привязки цитат было 10/22 (45%) при том
    же железе и той же модели — то есть три четверти прироста дала не модель, а
    обвязка вокруг неё.

    ### Чего этот замер НЕ обещает

    100% не будет. Устойчиво не даются однородные члены без союза, «не
    своевременно» слитно и запятая перед «однако» — модель либо не видит их,
    либо предлагает неверную правку. Числа плавают на 1-2 между прогонами:
    модель недетерминирована, и один и тот же текст даёт то 19, то 18 находок.
    """
    prompt = load_prompt("spellcheck")
    glossary_terms = _load_glossary_terms()
    if glossary_terms:
        prompt = f"{prompt}\nТермины компании (не считать ошибками): {', '.join(glossary_terms)}."

    # Первый проход — LanguageTool (детерминированный, без LLM): грамматика,
    # пунктуация, орфография по словарю (+ наш глоссарий терминов ПБ).
    # Ловит то, на чём LLM иногда либо тормозит, либо "исправляет" то, что
    # не было ошибкой. На весь документ разом — LT сам режет на предложения,
    # чанк-границы ему не нужны (см. tools/languagetool/, infrastructure/
    # languagetool.py, docs/08-references.md).
    if task:
        task.progress = "Проверяю через LanguageTool"
        task.percent = 3
    lt_errors = await languagetool.check(text)
    for e in lt_errors:
        e["chunk"] = 0

    if not deep:
        # Быстрый режим: правки уже есть, текст не переписываем. corrected_text
        # собирается применением найденных замен, а не отдельным проходом
        # модели — она переписывала бы весь документ со скоростью 12 токенов/с.
        errors = _dedup_errors(list(lt_errors))
        if task:
            task.percent = 95
        out = {
            "errors": errors,
            "corrected_text": _apply_to_text(text, errors),
            "stats": {
                "total_errors": len(errors),
                "by_type": _count_by_type(errors),
                "chunks_processed": 0,
                "режим": "быстрый (только LanguageTool)",
            },
        }
        await _attach_corrected_docx(out, errors, source_path, task)
        return out

    # Мелкая порция — главный рычаг качества, а не настройка производительности.
    # Замерено на 19 намеренно заложенных ошибках, одна модель и один промпт,
    # менялся только размер куска:
    #     20 предложений разом (было 300 слов)  —  5 из 19
    #     по 4 предложения                      —  9 из 14 на тех же пропущенных
    #     по одному предложению                 — 11 из 14 на тех же пропущенных
    # Модель не слабая, её заваливали объёмом: на большом куске она находит
    # 2-3 ошибки и останавливается, пропуская даже «обьекте» и «в течении».
    # По времени почти без разницы — платим за ВЫДАННЫЕ токены, а их столько
    # же (85 с против 95 с на том же тексте).
    chunks = await asyncio.to_thread(
        chunk_sentences, text, config.SPELLCHECK_CHUNK_WORDS, overlap_words=0
    )
    all_errors: list[dict] = list(lt_errors)

    for i, chunk in enumerate(chunks, start=1):
        if task:
            task.progress = f"Фрагмент {i}/{len(chunks)}"
        log.info("Spellcheck chunk %d/%d (%d words)", i, len(chunks), len(chunk.split()))
        chunk_base = 5 + int(90 * (i - 1) / len(chunks))
        chunk_span = max(1, int(90 / len(chunks)))
        result = await llm.chat_json(
            system=prompt,
            user=_with_known_errors(chunk, lt_errors),
            num_predict=config.LLM_NUM_PREDICT_SPELLCHECK,
            on_delta=make_progress_counter(
                task, config.LLM_NUM_PREDICT_SPELLCHECK, chunk_base, chunk_span
            ),
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

    # Привязка к исходнику ДО дедупликации: иначе цитата модели с уже
    # применённым исправлением («Наша компания — надёжный партнёр») сравнивалась
    # бы с находками LT в другом написании.
    all_errors = _keep_applicable(all_errors, text)
    all_errors = _dedup_errors(all_errors)
    # Исправленный текст собирается применением правок, а не отдельным
    # проходом модели: раньше её просили вернуть переписанный фрагмент
    # целиком, и она тратила на это выдачу вместо поиска ошибок.
    corrected_text = _apply_to_text(text, all_errors)

    out: dict = {
        "errors": all_errors,
        "corrected_text": corrected_text,
        "stats": {
            "total_errors": len(all_errors),
            "by_type": _count_by_type(all_errors),
            "chunks_processed": len(chunks),
        },
    }

    await _attach_corrected_docx(out, all_errors, source_path, task)
    return out


async def _attach_corrected_docx(
    out: dict, errors: list[dict], source_path: Path | None, task: Task | None
) -> None:
    """Исправленный документ для скачивания.

    Сборка не должна ронять всю проверку: даже если файл собрать не удалось,
    найденные ошибки и текст пользователю уже полезны.
    """
    if task:
        task.progress = "Готовлю исправленный документ"
    try:
        docx_path, edited_copy = await asyncio.to_thread(
            build_corrected_docx, out["corrected_text"], errors, source_path
        )
        out["_docx_path"] = docx_path.name
        # Владелец файла — тот, кто поставил задачу. Записываем здесь, а не в
        # истории: история пишется ПОСЛЕ завершения задачи, а файл существует
        # уже сейчас и до тех пор доступен был бы любому по имени.
        if task is not None and task.owner:
            await asyncio.to_thread(ownership.claim, docx_path.name, task.owner)
        out["_docx_is_copy"] = edited_copy
    except Exception:
        log.exception("Не удалось подготовить исправленный документ")


def _count_by_type(errors: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in errors:
        t = e.get("type", "?")
        out[t] = out.get(t, 0) + 1
    return out
