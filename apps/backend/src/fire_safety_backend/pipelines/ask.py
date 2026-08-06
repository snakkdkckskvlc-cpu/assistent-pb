"""Кнопка 5: свободный вопрос по документу.

Сотрудник загружает файл и спрашивает наспех: «дай информацию о сотрудниках».
Ответ собирается в две ступени.

### Почему две ступени, а не одна

Короткий вопрос не говорит, что именно выписывать. «Информация о сотрудниках»
для одного — это ФИО и должности, для другого — ещё и полномочия, доверенности,
контакты. Модель, получив такой вопрос вместе с договором на шесть страниц,
отвечает первым, что попалось.

Первая ступень переписывает вопрос в точное задание, ВТОРАЯ отвечает по
документу. Первой ступени документ не нужен — только вопрос и оглавление, —
поэтому она стоит секунды, а не минуты: платим мы за выданные токены, а их там
десятки. Общее время растёт незаметно.

Уточнённое задание возвращается пользователю вместе с ответом. Иначе это чёрный
ящик: человек не понимает, почему ответ такой, и не может поправить вопрос.

### Почему НЕ одним запросом на весь документ

Окно модели на этой машине 12288 токенов, и договор на шесть страниц влезает в
него целиком — то есть напрашивается один запрос вместо нескольких. Пробовали,
замерили, откатили: стало вдвое медленнее при том же или худшем качестве.

    вопрос            по кускам          одним запросом
    сотрудники        163 c, 2 ссылки    360 c, 0 ссылок
    сроки             166 c, 2 из 2      394 c, 2 из 2
    штрафы            185 c, 3 из 3      258 c, 2 из 2

На процессоре большое окно дорого само по себе: экономия на числе запросов не
покрывает роста KV-кэша. Рассуждение из pipelines/legal.py («шире окно — меньше
частей — быстрее») здесь не работает, потому что там каждая часть выдаёт полный
бюджет ответа, а тут ответ короткий и режется дёшево.

### Откуда берутся номера страниц

Настоящие страницы есть только у PDF — парсер отдаёт его текст постранично
(infrastructure/parsers). У DOCX и вставленного текста страниц не существует:
разбиение на страницы там появляется только при печати и зависит от шрифта и
полей. Поэтому куски таких документов нумеруются как «фрагмент N», а не «стр.
N» — писать «стр. 3» для DOCX значило бы придумать номер, которого нет, и
человек пошёл бы искать по нему место в файле.

Цитаты проверяются по тексту куска: модель, придумавшая цитату или номер
страницы, лишает ответ единственного способа проверки.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from .. import config
from ..infrastructure import llm
from ._prompts import load_prompt, make_progress_counter

if TYPE_CHECKING:
    from pathlib import Path

    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)

# Оглавление для первой ступени: ей нужен не документ, а представление о том,
# что в нём есть. Больше — только замедлит, не улучшив задание.
_OUTLINE_CHARS = 1500

# Кусок документа для второй ступени. Держится заведомо меньше окна модели: в
# запрос идут ещё промпт, задание и место под ответ.
_BLOCK_CHARS = 3500

# Цитата короче этого не проверяема: пять слов найдутся в любом договоре.
_MIN_QUOTE_CHARS = 12

_HEADING_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+\S.*|[А-ЯЁ][А-ЯЁ \-]{6,})$", re.MULTILINE)


def _outline(text: str) -> str:
    """Оглавление документа: заголовки, если они есть, иначе начало текста."""
    headings = [h.strip() for h in _HEADING_RE.findall(text)][:40]
    if len(headings) >= 3:
        return "\n".join(headings)
    return text[:_OUTLINE_CHARS]


def _pdf_pages(source_path: Path | None) -> list[str]:
    """Страницы PDF или пустой список. Ошибку не поднимаем: не смогли разобрать
    постранично — ответим по фрагментам, это хуже, но не отказ."""
    if source_path is None or source_path.suffix.lower() != ".pdf":
        return []
    try:
        from ..infrastructure import secure_files
        from ..infrastructure.parsers import extract_pdf_pages

        with secure_files.plaintext(source_path) as readable:
            return [p for p in extract_pdf_pages(readable)]
    except Exception as e:  # noqa: BLE001
        log.warning("Не удалось разобрать PDF постранично (%s) — отвечаю по фрагментам", e)
        return []


def _split_by_size(text: str, label_prefix: str) -> list[tuple[str, str]]:
    """Режет текст на куски по абзацам, не разрывая их посередине."""
    blocks: list[tuple[str, str]] = []
    current = ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) + 2 > _BLOCK_CHARS:
            blocks.append((f"{label_prefix} {len(blocks) + 1}", current.strip()))
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        blocks.append((f"{label_prefix} {len(blocks) + 1}", current.strip()))
    return blocks


def build_blocks(text: str, source_path: Path | None = None) -> tuple[list[tuple[str, str]], str]:
    """Куски документа с ЧЕСТНЫМИ метками места и предупреждение для человека.

    Для PDF метка — настоящий номер страницы. Для всего остального — номер
    фрагмента: страниц у DOCX и вставленного текста не существует, и выдавать
    номер фрагмента за номер страницы нельзя. Человек пойдёт искать по нему
    место в файле и не найдёт.
    """
    pages = _pdf_pages(source_path)
    meaningful = [p for p in pages if p.strip()]
    if pages and len(meaningful) >= len(pages) / 2:
        blocks = [
            (f"стр. {i}", page.strip()) for i, page in enumerate(pages, start=1) if page.strip()
        ]
        if blocks:
            return blocks, ""
    warning = ""
    if source_path is not None and source_path.suffix.lower() == ".pdf":
        warning = (
            "Текстового слоя в PDF нет или он неполный — ссылки даны на фрагменты, "
            "а не на страницы файла."
        )
    elif source_path is not None:
        warning = (
            f"У формата {source_path.suffix.lower()} нет номеров страниц — "
            "ссылки даны на фрагменты документа."
        )
    else:
        warning = "Текст вставлен вручную — ссылки даны на фрагменты, а не на страницы."
    return _split_by_size(text, "фрагмент"), warning


def _normalize(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def verify_sources(sources: list[dict], blocks: list[tuple[str, str]]) -> list[dict]:
    """Помечает каждый источник признаком `проверено`.

    Ответ по документу ценен ровно тем, что его можно проверить. Если цитата в
    документе не находится или указано несуществующее место, показывать это как
    подтверждение нельзя — иначе выдуманная ссылка выглядит убедительнее
    настоящей.
    """
    by_label = {label.casefold(): _normalize(body) for label, body in blocks}
    checked: list[dict] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        quote = _normalize(src.get("цитата", ""))
        place = str(src.get("место", "")).strip()
        if len(quote) < _MIN_QUOTE_CHARS:
            checked.append(
                {**src, "проверено": False, "почему": "цитата слишком короткая для проверки"}
            )
            continue
        if quote in by_label.get(place.casefold(), ""):
            checked.append({**src, "проверено": True})
            continue
        # Модель устойчиво пишет «стр. 1» там, где место называется «фрагмент 1»
        # (наблюдалось на живом прогоне). Цитата при этом настоящая. Если она
        # встречается РОВНО в одном куске, место не забраковываем, а исправляем:
        # выбросить верную ссылку из-за неверной подписи — потеря на ровном
        # месте, а человеку нужно попасть в нужный кусок.
        hits = [label for label, body in blocks if quote in _normalize(body)]
        if len(hits) == 1:
            checked.append(
                {
                    **src,
                    "место": hits[0],
                    "проверено": True,
                    "почему": f"место уточнено: модель указала «{place}»" if place else "",
                }
            )
        elif hits:
            checked.append(
                {**src, "проверено": False, "почему": "цитата встречается в нескольких местах"}
            )
        else:
            checked.append({**src, "проверено": False, "почему": "цитата в документе не найдена"})
    return checked


async def refine_question(question: str, text: str, task: Task | None = None) -> dict:
    """Первая ступень: вопрос наспех → точное задание. Документ не читает."""
    if task:
        task.progress = "Уточняю вопрос"
        task.percent = 3
    user = f"ВОПРОС СОТРУДНИКА:\n{question.strip()}\n\nОГЛАВЛЕНИЕ ДОКУМЕНТА:\n{_outline(text)}"
    result = await llm.chat_json(
        system=load_prompt("ask_refine"),
        user=user,
        temperature=config.LLM_TEMPERATURE_ASK,
        num_predict=config.LLM_NUM_PREDICT_ASK_REFINE,
    )
    refined = str(result.get("задание", "")).strip()
    if not refined:
        # Ступень не справилась — работаем по исходному вопросу. Молча
        # подменять задание нечем, но и отказывать из-за этого незачем.
        log.warning("Уточнение вопроса не дало задания — беру исходный вопрос")
        return {"задание": question.strip(), "искать": [], "форма_ответа": ""}
    return {
        "задание": refined,
        "искать": [str(x) for x in result.get("искать", []) if str(x).strip()][:12],
        "форма_ответа": str(result.get("форма_ответа", "")).strip(),
    }


def answer_text(value: object) -> str:
    """Ответ модели в виде текста для человека.

    Модель возвращает «ответ» то строкой, то СПИСКОМ строк — второе особенно
    охотно, когда уточнение просит «форму ответа: список». Наблюдалось на живом
    прогоне: пользователю показывалось `['Начало работ — ...', 'Окончание ...']`
    вместе с квадратными скобками и кавычками, то есть питоновский синтаксис
    вместо ответа. Схему в промпте это не чинит: модель отвечает так, как ей
    удобнее, и принимать оба вида дешевле, чем воевать.
    """
    if isinstance(value, list):
        items = [answer_text(v) for v in value]
        return "\n".join(f"— {i}" for i in items if i)
    if isinstance(value, dict):
        return "\n".join(f"{k}: {answer_text(v)}" for k, v in value.items())
    return str(value or "").strip()


def rescue_misplaced_sources(result: dict) -> dict:
    """Переносит источники из поля «ответ» туда, где им место.

    Третья форма ответа, которую выдаёт модель помимо строки и списка строк:
    она кладёт в «ответ» СПИСОК ИСТОЧНИКОВ, а «источники» оставляет пустым.
    Наблюдалось на замере: на вопрос «сколько человек в бригаде» пользователь
    получил бы текст «цитата: … в лице зам. директора Ковалёва И. П. …» при
    пустой таблице ссылок — правдоподобно выглядящий ответ не на тот вопрос и
    без единого подтверждения.

    Схему промптом это не чинит: модель отвечает так, как ей удобнее. Дешевле
    разложить обратно, чем воевать, — тогда цитаты проходят обычную проверку по
    тексту документа и попадают в таблицу, а не притворяются ответом.
    """
    raw = result.get("ответ")
    if not isinstance(raw, list):
        return result
    misplaced = [x for x in raw if isinstance(x, dict) and ("цитата" in x or "место" in x)]
    if not misplaced:
        return result
    rest = [x for x in raw if x not in misplaced]
    sources = [s for s in result.get("источники", []) if isinstance(s, dict)]
    return {**result, "ответ": rest, "источники": sources + misplaced}


def _merge_answers(answers: list[dict]) -> dict:
    """Склеивает ответы по кускам. Куски, где ничего не нашлось, отбрасываются
    целиком: «в этом фрагменте не найдено» повторённое десять раз — это шум,
    а не ответ."""
    found: list[tuple[str, list]] = []
    for a in answers:
        if not a.get("найдено"):
            continue
        text = answer_text(a.get("ответ"))
        sources = [s for s in a.get("источники", []) if isinstance(s, dict)]
        # Пустой текст при непустых цитатах — не повод выбрасывать кусок:
        # цитаты и есть самое ценное, а связный ответ соберётся из них.
        if text or sources:
            found.append((text, sources))
    if not found:
        return {"ответ": "", "источники": [], "найдено": False}
    sources = [s for _, block_sources in found for s in block_sources]
    text = "\n\n".join(t for t, _ in found if t)
    if not text and sources:
        text = "Найденное приведено ниже цитатами из документа."
    return {"ответ": text, "источники": sources, "найдено": True}


async def run_ask(
    question: str,
    text: str,
    task: Task | None = None,
    source_path: Path | None = None,
) -> dict:
    """Свободный вопрос по документу. Ответ — только из документа, со ссылками.

    Замер времени определяется вторым проходом: первая ступень видит лишь
    вопрос и оглавление и укладывается в секунды.
    """
    blocks, place_warning = build_blocks(text, source_path)
    if not blocks:
        return {
            "вопрос": question.strip(),
            "ответ": "Документ пуст — отвечать не по чему.",
            "источники": [],
            "найдено": False,
        }

    refined = await refine_question(question, text, task)
    answer_prompt = load_prompt("ask_answer")

    # Исходный вопрос идёт ГЛАВНЫМ, уточнение — подсказкой. Замерено на живом
    # прогоне: на вопрос «дай информацию о сотрудниках» первая ступень выдавала
    # задание «о сотрудниках, УЧАСТВУЮЩИХ В ВЫПОЛНЕНИИ РАБОТ», и вторая честно
    # отвечала «не найдено» — представители сторон, подписавшие договор, под
    # приписанное условие не подходят. Прямой запрет сужать вопрос в промпте
    # первой ступени модель проигнорировала (проверено с этим же примером в
    # тексте промпта).
    #
    # Поэтому сужение больше не может ничего отсечь: вторая ступень видит то,
    # что человек спросил на самом деле, а уточнение только добавляет, что
    # искать.
    # Задание первой ступени идёт во вторую — и это не украшение, а то, ради
    # чего ступень существует. Замерено на трёх вариантах одного фрагмента:
    #
    #   вопрос + список «что искать»   -> не найдено
    #   ТОЛЬКО вопрос пользователя     -> не найдено
    #   вопрос + «Выпиши всех людей,
    #   названных в этом фрагменте»    -> находит обоих подписантов
    #
    # То есть голый вопрос «дай информацию о сотрудниках» не работает сам по
    # себе: модель читает «сотрудники» как штат работников, а подписанты
    # договора под это слово не подходят, и её прочтение защитимо. Вытягивает
    # именно КОНКРЕТНАЯ повелительная формулировка.
    #
    # Отсюда требование к первой ступени: она обязана переводить житейское
    # слово на язык документов, а не пересказывать его (см. ask_refine.txt).
    task_line = f"ВОПРОС СОТРУДНИКА: {question.strip()}"
    if refined["задание"] and refined["задание"] != question.strip():
        task_line += f"\n\nЧто это значит: {refined['задание']}"
    if refined["искать"]:
        task_line += "\n\nСреди прочего искать:\n" + "\n".join(f"- {x}" for x in refined["искать"])
    if refined["форма_ответа"]:
        task_line += f"\n\nФорма ответа: {refined['форма_ответа']}."

    answers: list[dict] = []
    for i, (label, body) in enumerate(blocks, start=1):
        if task:
            task.progress = f"Читаю {label} ({i}/{len(blocks)})"
        base = 5 + int(90 * (i - 1) / len(blocks))
        span = max(1, int(90 / len(blocks)))
        result = await llm.chat_json(
            system=answer_prompt,
            user=f"ЗАДАНИЕ:\n{task_line}\n\nДОКУМЕНТ:\n[{label}]\n{body}",
            temperature=config.LLM_TEMPERATURE_ASK,
            num_predict=config.LLM_NUM_PREDICT_ASK,
            on_delta=make_progress_counter(task, config.LLM_NUM_PREDICT_ASK, base, span),
        )
        if isinstance(result, dict):
            answers.append(rescue_misplaced_sources(result))

    merged = _merge_answers(answers)
    sources = verify_sources(merged["источники"], blocks)
    if task:
        task.percent = 98
    out = {
        "вопрос": question.strip(),
        # Человеку показывается то же, что ушло в модель: он должен видеть, как
        # понят его вопрос, и переспросить иначе, если понят не так.
        "уточнённое_задание": task_line,
        "ответ": merged["ответ"]
        or "В документе таких сведений не найдено. Уточните вопрос или проверьте, тот ли файл.",
        "источники": sources,
        "найдено": merged["найдено"],
        "stats": {
            "кусков": len(blocks),
            "ссылок": len(sources),
            "подтверждено": sum(1 for s in sources if s.get("проверено")),
            "нумерация": "страницы" if blocks and blocks[0][0].startswith("стр.") else "фрагменты",
        },
    }
    if place_warning:
        out["_place_warning"] = place_warning
    return out


__all__ = [
    "build_blocks",
    "refine_question",
    "rescue_misplaced_sources",
    "run_ask",
    "verify_sources",
]
