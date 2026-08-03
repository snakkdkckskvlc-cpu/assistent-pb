"""Кнопка 1: проверка орфографии и пунктуации."""

from __future__ import annotations

import asyncio
import logging
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
    return (
        f"{chunk}\n\n"
        f"(Проверка по словарю уже нашла здесь: {listed}. "
        f"Их повторять не нужно — ищи то, что она пропустила.)"
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


def _dedup_errors(errors: list[dict]) -> list[dict]:
    """LT и LLM иногда репортят одну и ту же ошибку — LT детерминирован,
    при конфликте оставляем его и отбрасываем совпавший LLM-дубликат."""
    lt_errors = [e for e in errors if e.get("source") == "languagetool"]
    other_errors = [e for e in errors if e.get("source") != "languagetool"]
    lt_normalized = [n for n in (_normalize_before(e.get("before", "")) for e in lt_errors) if n]

    deduped = list(lt_errors)
    for e in other_errors:
        norm = _normalize_before(e.get("before", ""))
        is_dup = bool(norm) and any(norm in lt_n or lt_n in norm for lt_n in lt_normalized)
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

    Замер на 29 намеренно заложенных ошибках в четырёх деловых письмах:

        LanguageTool   14/29 (48%)      1,7 с
        модель 7B      16/29 (55%)    117 с
        вместе         23/29 (79%)    117 с

    Ловят они РАЗНОЕ: девять ошибок нашла только модель (все контекстные —
    вводные обороты, «что бы» против «чтобы», причастный оборот), семь —
    только LanguageTool. Поэтому быстрый режим не заменяет глубокий, а даёт
    мгновенный результат там, где ждать две минуты на страницу незачем.
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
