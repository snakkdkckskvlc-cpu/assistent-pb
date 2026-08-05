"""Кнопка 1: проверка орфографии и пунктуации."""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
from collections import Counter
from typing import TYPE_CHECKING

from fire_safety_rag import chunk_sentences

from .. import config
from ..infrastructure import languagetool, llm, ru_rules
from ..infrastructure.generators.corrected_docx import build_corrected_docx
from ..services import ownership
from ._prompts import load_prompt, make_progress_counter

if TYPE_CHECKING:
    from pathlib import Path

    from ..infrastructure.queue import Task

log = logging.getLogger(__name__)


def _load_glossary_terms() -> list[str]:
    """Единый источник фирменных терминов для промпта модели.

    Тот же файл сверяется с находками LanguageTool — но НЕ его собственным
    механизмом словаря: тот молча не работает, разбор в
    infrastructure/languagetool.py::_glossary_lookup. Список читается из одного
    места, чтобы термин добавлялся один раз и не расходился между промптом и
    проверкой.
    """
    return languagetool.glossary_terms()


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

# Источники, чьи цитаты дословны по построению и чьи находки при конфликте
# побеждают находку модели: словарь LanguageTool и домашние правила проекта.
# Оба детерминированы — на одном тексте всегда один и тот же ответ.
_DETERMINISTIC_SOURCES = frozenset({"languagetool", ru_rules.SOURCE})


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


# Числовая группа целиком, вместе с внутренними разделителями: сравнивать
# отдельные цифры недостаточно. «2.2» и «2,2» состоят из одних и тех же цифр,
# а это разные вещи — номер пункта договора и дробь.
_NUMERIC_RUN = re.compile(r"[\d.,]*\d[\d.,]*")
_ABBREVIATIONS = re.compile(r"\b[А-ЯЁA-Z]{2,}(?:-\d+)?\b")


def _numbers(text: str) -> list[str]:
    """Числа текста. Крайние точки и запятые срезаются: они принадлежат
    предложению, а не числу, — иначе законная правка «100 однако» →
    «100, однако» выглядела бы подменой числа."""
    return [run.strip(".,") for run in _NUMERIC_RUN.findall(text) if run.strip(".,")]


def _atomic_edits(before: str, after: str) -> list[tuple[str, str]]:
    """Разбирает правку модели на ОТДЕЛЬНЫЕ изменения.

    Модель цитирует не слово, а половину предложения, и кладёт в одну правку
    несколько изменений сразу. Замерено на размеченном наборе: «Как Вам
    известно наша организация выполняет работы по монтажу автоматической...» →
    та же фраза с верной запятой после вводного оборота И заодно с подменой
    слов дальше по предложению.

    Судить такую пару целиком нельзя ни так, ни эдак: принять — протащишь
    подмену, отвергнуть — потеряешь верную запятую. Замерено, что второе стоит
    13 процентных пунктов полноты (94% → 81%).

    Поэтому пара раскладывается на отдельные изменения, и каждое проверяется
    само по себе: запятая проходит, подмена слова отсекается. Одно слово
    контекста с каждой стороны — по нему правка находится в документе, и
    человеку в таблице видно место, а не голый знак.
    """
    b, a = before.split(), after.split()
    edits: list[tuple[str, str]] = []

    def add(bi1: int, bi2: int, aj1: int, aj2: int, *, left: bool, right: bool) -> None:
        """Контекст берётся ТОЛЬКО из неизменённых кусков.

        Внутри изменённого блока соседнее слово на двух сторонах разное, и,
        взяв его в контекст, мы сравнивали бы не то: правка выглядела бы
        меняющей состав слов, даже если она ставит одну запятую.
        """
        edits.append(
            (
                " ".join(b[bi1 - 1 if left else bi1 : bi2 + 1 if right else bi2]),
                " ".join(a[aj1 - 1 if left else aj1 : aj2 + 1 if right else aj2]),
            )
        )

    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=b, b=a, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        # Блок одинаковой длины разбирается ПОСЛОВНО. difflib склеивает соседние
        # изменения в один replace, и верная правка снова уезжала вместе с
        # неверной: «сообщаем следующее. Договор заключенный» → «сообщаем
        # следующее: договор, заключенный» — тут и точка на двоеточие (нельзя),
        # и смена регистра (нельзя), и запятая при причастном обороте (можно).
        # Слово к слову они разделяются, и запятая уцелевает.
        if tag == "replace" and (i2 - i1) == (j2 - j1) and (i2 - i1) > 1:
            for k in range(i2 - i1):
                if b[i1 + k] != a[j1 + k]:
                    add(
                        i1 + k,
                        i1 + k + 1,
                        j1 + k,
                        j1 + k + 1,
                        # Слева контекст есть только у первого слова блока,
                        # справа — только у последнего: остальные соседи сами
                        # изменены и в контекст не годятся.
                        left=k == 0 and i1 > 0,
                        right=k == i2 - i1 - 1 and i2 < len(b),
                    )
            continue
        add(i1, i2, j1, j2, left=i1 > 0, right=i2 < len(b) and j2 < len(a))
    return edits or [(before, after)]


def _skeleton(text: str) -> str:
    """Буквы и цифры без всего остального — то, что правка менять не должна,
    если она про пунктуацию."""
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE).casefold()


def _letter_words(text: str) -> list[str]:
    return re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)


def _punctuation(text: str) -> Counter[str]:
    return Counter(ch for ch in text if not ch.isalnum() and not ch.isspace())


# Знаки, которые корректор вправе добавлять и убирать. Двоеточия и точки с
# запятой здесь нет намеренно: их расстановка — вопрос смысла предложения, а не
# орфографии, и модель на договорах ошибалась в ней постоянно.
#
# Точки здесь тоже нет, и это осознанный размен. Пропущенная точка в конце
# предложения останется ненайденной — зато модель не сможет ни снять точку
# (наблюдалось: «...контроль толщины покрытия.» → без точки), ни расставить их
# по своему усмотрению в перечислениях договора. Потеря заметна человеку сразу,
# лишняя или пропавшая точка в договоре — нет.
_EDITABLE_MARKS = frozenset({",", "—", "–", "-"})

# Правка одного слова: «обьекте» → «объекте». Больше двух отличий — это уже не
# опечатка, а подмена слова.
_MAX_WORD_EDITS = 2


def _levenshtein(a: str, b: str, cap: int) -> int:
    """Расстояние редактирования, но не больше cap — точное значение дальше
    неинтересно, а обрыв бережёт время на длинных строках."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _edit_shape_reason(before: str, after: str) -> str | None:
    """Допустима ли ФОРМА правки. None — допустима.

    ### Зачем ограничивать форму, а не содержание

    Запреты на числа, аббревиатуры и «ё» выросли из конкретных наблюдавшихся
    случаев. Так можно закрывать дыры бесконечно и всё равно не угадать
    следующую: на каждом новом документе модель придумывает новое.

    Здесь наоборот — перечислено, что корректору ВООБЩЕ можно, а всё остальное
    запрещено. Такое ограничение не зависит от текста, поэтому работает на
    договоре так же, как на письме.

    Корректор вправе сделать ровно две вещи:

    1. Поставить или снять запятую либо тире — в том числе слитно/раздельно
       («что бы» → «чтобы»): буквы при этом не меняются.
    2. Исправить написание ОДНОГО слова, отличающееся на пару букв
       («обьекте» → «объекте»).

    Всё прочее — не орфография и не пунктуация. Наблюдалось на настоящем
    договоре: «а Заказчик — принять» → «а Заказчик: принять» (тире на
    двоеточие), «доверенности, и ООО» → «доверенности; ООО» (запятая на точку
    с запятой), пропажа точки в конце предложения, «НДС 20 %» → «НДС 20%».
    Каждая из них внешне похожа на работу корректора и ни одна ею не является.
    """
    if _skeleton(before) == _skeleton(after):
        added = _punctuation(after) - _punctuation(before)
        removed = _punctuation(before) - _punctuation(after)
        forbidden = (set(added) | set(removed)) - _EDITABLE_MARKS
        if forbidden:
            return f"меняет знаки, которые корректору не подчиняются: {''.join(sorted(forbidden))}"
        if _letter_words(before) != _letter_words(after) and [
            w.casefold() for w in _letter_words(before)
        ] == [w.casefold() for w in _letter_words(after)]:
            # Буквы те же с точностью до регистра — значит правка заодно
            # переписала заглавную. Наблюдалось на договоре: «заключили
            # настоящий Договор о нижеследующем» → «настоящий договор, о
            # нижеследующем», где «Договор» — определённый термин договора.
            # Настоящие ошибки регистра ловит LanguageTool (категория CASING),
            # и его находки сюда не попадают.
            return "меняет регистр слова"
        if not added and not removed and _letter_words(before) == _letter_words(after):
            # Буквы те же, знаки те же — значит правка только в пробелах, и это
            # форматирование, а не орфография. Слитное/раздельное написание сюда
            # НЕ попадает: там меняется состав слов («что бы» → «чтобы»).
            return "меняет только пробелы"
        return None

    before_words, after_words = _letter_words(before), _letter_words(after)
    if len(before_words) != len(after_words):
        return "меняет состав слов"
    differing = [(x, y) for x, y in zip(before_words, after_words, strict=True) if x != y]
    if len(differing) != 1:
        return "меняет больше одного слова"
    x, y = differing[0]
    if _levenshtein(x.casefold(), y.casefold(), _MAX_WORD_EDITS) > _MAX_WORD_EDITS:
        return "подменяет слово, а не исправляет написание"
    return None


def _unsafe_reason(before: str, after: str) -> str | None:
    """Почему эту правку нельзя применять. None — правка допустимая.

    ### Откуда взялось

    Замер на НАСТОЯЩЕМ договоре (не на размеченных письмах, а на вычитанном
    юридическом тексте, который писали не мы): пайплайн выдал 14 находок, и
    почти все от модели оказались выдумкой. Среди них — «2.2. Авансирование» →
    «2,2. Авансирование», то есть порча номера пункта договора.

    Для инструмента, которым правят договоры с заказчиком, это хуже пропуска:
    пропущенную запятую человек переживёт, испорченный номер пункта — нет.

    Проверка орфографии и пунктуации не вправе менять СОДЕРЖАНИЕ. Отсюда три
    запрета, и каждый на конкретный наблюдавшийся случай:

    1. Цифры. Корректор не меняет числа: ни номера пунктов, ни суммы, ни даты.
    2. Аббревиатуры. «ФЗ» → «Федерального закона» — это уже не орфография.
       Раскрывать сокращения корректор тоже не вправе.
    3. Буква «ё». «зачёта» → «зачета» — не исправление, а порча: обратное
       направление (е → ё) допустимо, оно восстанавливает букву.
    """
    if _numbers(before) != _numbers(after):
        return "меняет числа"
    # Считаем количество, а не наличие. «сертификат соответствия ФЗ ... № 123-ФЗ»
    # → «...Федерального закона... № 123-ФЗ»: одно вхождение ФЗ исчезло, но
    # второе осталось, и проверка по множеству такую подмену пропускала.
    if Counter(_ABBREVIATIONS.findall(before)) - Counter(_ABBREVIATIONS.findall(after)):
        return "теряет аббревиатуру"
    if before.replace("ё", "е").replace("Ё", "Е") == after and "ё" in before.lower():
        return "убирает букву ё"
    return _edit_shape_reason(before, after)


def _keep_applicable(errors: list[dict], text: str) -> list[dict]:
    """Оставляет только правки, которые реально применятся к документу.

    Отбрасываются две породы находок модели: пустышки (`before` совпадает с
    `after` — «исправление», ничего не меняющее) и выдуманные цитаты, которых
    в документе нет. И то и другое выглядит в списке как работа, а документ не
    меняет.
    """
    kept: list[dict] = []
    for e in errors:
        if e.get("source") in _DETERMINISTIC_SOURCES:
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
        # Раскладываем ДО проверки формы: в одной цитате модели обычно и верная
        # запятая, и подмена слова рядом. Целиком такую пару нельзя ни принять,
        # ни отвергнуть без потери.
        for part_before, part_after in _atomic_edits(anchored, after):
            part_anchored = _anchor_to_source(part_before, text)
            if part_anchored is None:
                continue
            if _normalize_before(part_anchored) == _normalize_before(part_after):
                continue
            unsafe = _unsafe_reason(part_anchored, part_after)
            if unsafe is not None:
                log.warning(
                    "Правка модели %s, отбрасываю: %r -> %r",
                    unsafe,
                    part_anchored[:60],
                    part_after[:60],
                )
                continue
            kept.append({**e, "before": part_anchored, "after": part_after})
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
    lt_errors = [e for e in errors if e.get("source") in _DETERMINISTIC_SOURCES]
    other_errors = [e for e in errors if e.get("source") not in _DETERMINISTIC_SOURCES]
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

    31 намеренно заложенная ошибка в трёх деловых письмах
    (apps/backend/tests/fixtures/spellcheck/), qwen2.5:7b-instruct:

        LanguageTool + правила    20/31 (65%)     5 с
        вместе с моделью          28/31 (90%)   655 с

    Ловят они РАЗНОЕ, и это главная причина держать оба прохода: LanguageTool
    закрывает орфографию по словарю целиком и почти не видит пунктуацию;
    модель — наоборот, берёт контекстное обособление, которое правилами не
    поймать. Поэтому быстрый режим не заменяет глубокий, а даёт мгновенный
    результат там, где ждать десять минут незачем.

    ### Полнота — не единственное число, и не главное

    Полноту легко поднять, разрешив модели «находить» больше. Поэтому рядом
    меряется шум на ЧУЖОМ вычитанном тексте, где находок быть не должно:

        python scripts/evaluate_spellcheck.py --noise <файл>

    На настоящем договоре (2000 символов) — 4 находки, из них 2 от модели.
    До запретов на форму правки было 14, из них 12 от модели, и почти все
    неверные, включая «2.2. Авансирование» → «2,2. Авансирование».

    Размен записан честно: запреты стоили 13 процентных пунктов полноты
    (94% → 81%), из них 6 отыграно разбором правки на отдельные изменения и
    правилом на причастный оборот. Для инструмента, которым правят договоры с
    заказчиком, размен выбран в пользу документа: пропущенную запятую человек
    переживёт, испорченный номер пункта — нет.

    ### Чего этот замер НЕ обещает

    100% не будет. Осталось три пропуска, и оба класса упираются не в усердие:

    - однородные члены без союза («монтаж наладку и сдачу») — чтобы отличить их
      от несвязанных слов, нужен морфологический разбор, а зависимости в
      хрупкий установщик проект не тянет;
    - «что бы» против «чтобы» — правилом неотличимо от «что бы вы
      посоветовали», а правило, которое угадывает, портит текст.

    Числа плавают на 1-2 между прогонами даже при нулевой температуре. И письма
    писали мы: это индикатор, а не аттестация.
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
    # Домашние правила идут рядом со словарём, а не вместо него: они закрывают
    # ровно тот класс, который не берут ни LanguageTool, ни модель (см.
    # infrastructure/ru_rules.py). Стоят ноль секунд, поэтому работают в обоих
    # режимах, включая быстрый.
    lt_errors.extend(ru_rules.check(text))
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
            temperature=config.LLM_TEMPERATURE_SPELLCHECK,
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
