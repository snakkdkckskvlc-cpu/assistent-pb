"""Извлечение текстового слоя PDF, постранично, с разметкой структуры.

Страница разбирается по трём путям, в порядке убывания надёжности:

1. Есть таблицы С РАМКАМИ — они вынимаются отдельно и отдаются markdown-
   таблицей `| кол1 | кол2 |`. Это и компактнее, и однозначнее: `layout=True`
   на той же странице раздувает текст в 2.2 раза (замер на положении НЛМК:
   2497 → 5479 символов), а связь «ячейка ↔ колонка» модель всё равно должна
   восстанавливать по пробелам.
2. Таблица БЕЗ рамок — остаётся `layout=True`. Текстовая стратегия
   pdfplumber (`vertical_strategy="text"`) здесь проверена и отвергнута: она
   считает таблицей всю страницу и рубит обычную прозу посреди слов —
   «П 057 | 57665-SС-084-0176-2023 с изм | . №1 и №2 Положение о».
3. Обычная страница — построчно, с разметкой заголовков и списков.

Почему layout нельзя включить везде: он добивает каждую строку пробелами до
ширины страницы и раздувает текст в 2.6–6 раз (17-страничный договор: ~12k →
~32k токенов при окне модели 8k) — это втрое больше частей и втрое дольше
юр. анализ.
"""

from __future__ import annotations

import collections
import logging
import re
from typing import TYPE_CHECKING

import pdfplumber

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

# Разрыв между символами, с которого считаем, что это граница колонок, а не
# межсловный пробел. Подобрано на реальных документах компании: при 18pt
# проза даёт долю «колоночных» строк ≤ 0.04, таблицы — ≥ 0.09.
_COLUMN_GAP_PT = 18
# Доля строк с 2+ такими разрывами, с которой считаем страницу табличной.
# 0.07 — с запасом с обеих сторон от замеров выше.
_COLUMNAR_LINE_RATIO = 0.07
# Рамочная таблица меньше этого размера — почти всегда ложное срабатывание
# (например, e-mail «pozh-master@mail.ru» pdfplumber принимает за таблицу
# из одной строки в шесть колонок).
_MIN_TABLE_ROWS = 3
_MIN_TABLE_COLS = 2

# Во сколько раз кегль строки должен превышать основной кегль документа,
# чтобы считать строку заголовком. Абсолютный порог «> 14pt» из первоначальной
# постановки не годится: замерено, что в 69-ФЗ символов крупнее 14pt НЕТ
# ВООБЩЕ при основном кегле 11pt, а в положении НЛМК их один на 24 тысячи.
# Заголовки надо искать относительно текста документа, а не в пунктах.
_HEADING_SIZE_RATIO = 1.15
# Заголовок, набранный жирным тем же кеглем, дополнительно ограничен длиной:
# в положении НЛМК жирным выделены и заголовки («3. ТЕРМИНЫ И ОПРЕДЕЛЕНИЯ»,
# «5. РОЛИ»), и перечисления терминов ВНУТРИ предложения («акт; виза;
# Компания, оформление документа; согласование; транспортное средство.»).
# Разделяет их длина строки и отсутствие завершающей пунктуации.
_BOLD_HEADING_MAX_CHARS = 70
# Доля жирных символов, с которой строка считается целиком жирной.
_BOLD_LINE_RATIO = 0.8

# Типографские маркеры списка В САМОМ ТЕКСТЕ — только они заменяются на «- ».
#
# Буквенные и числовые маркеры («а)», «б)», «17)») сюда НЕ входят намеренно.
# Замерено: при их замене из ПП-1479 пропало по 70 вхождений «а)», «б)» и
# «г)» — а в нормативных актах это адресуемые единицы, на которые ссылаются
# («подпункт б) пункта 17»). Markdown они и так не ломают: строка вида
# «17) текст» — уже нумерованный список.
#
# Отступ слева тоже не используется: в 69-ФЗ строки с увеличенным x0
# (85 → 112) — это красная строка абзаца, а не пункт списка, и «- » здесь
# придумывало бы структуру, которой в документе нет.
_LIST_MARKER_RE = re.compile(r"^\s*([•‣▪·∙◦]|[-–—](?=\s))\s*")

# Признаки настоящей двухколоночной вёрстки (не таблицы): сплошной
# вертикальный просвет через бОльшую часть высоты страницы.
_COLUMN_GUTTER_MIN_PT = 100
_COLUMN_MIN_WORDS_PER_SIDE = 15
_COLUMN_MIN_HEIGHT_RATIO = 0.6


def _has_bordered_table(page) -> bool:
    return bool(_bordered_tables(page))


def _bordered_tables(page) -> list:
    """Таблицы, найденные ПО ЛИНИЯМ разметки.

    Текстовая стратегия сюда не подключается сознательно — см. докстринг
    модуля: на бесрамочных страницах она разрушает обычный текст.
    """
    try:
        tables = page.find_tables()
    except Exception:
        return []
    return [
        t for t in tables if len(t.rows) >= _MIN_TABLE_ROWS and len(t.columns) >= _MIN_TABLE_COLS
    ]


def _looks_columnar(page) -> bool:
    """Таблица без рамок: много строк, где текст разбит на 3+ колонки."""
    try:
        lines = page.extract_text_lines()
    except Exception:
        return False
    if not lines:
        return False
    columnar = 0
    for line in lines:
        chars = sorted(line.get("chars", ()), key=lambda c: c["x0"])
        gaps = sum(
            1 for a, b in zip(chars, chars[1:], strict=False) if b["x0"] - a["x1"] > _COLUMN_GAP_PT
        )
        if gaps >= 2:
            columnar += 1
    return columnar / len(lines) >= _COLUMNAR_LINE_RATIO


def _cell(value: str | None) -> str:
    """Ячейка в одну строку: переносы внутри ячейки сломали бы markdown-таблицу,
    а вертикальная черта — разметку колонок."""
    return " ".join(str(value or "").split()).replace("|", "\\|")


def _table_to_markdown(rows: list[list[str | None]]) -> str:
    """Таблица pdfplumber → markdown вида `| кол1 | кол2 |`.

    Первая строка считается заголовком. Ширина выравнивается по самой широкой
    строке: markdown с рваным числом колонок часть парсеров ломает, а модель
    сбивается на сопоставлении значения со столбцом.
    """
    cleaned = [[_cell(c) for c in row] for row in rows if any(_cell(c) for c in row)]
    if not cleaned:
        return ""
    width = max(len(r) for r in cleaned)
    cleaned = [r + [""] * (width - len(r)) for r in cleaned]
    head, *body = cleaned
    out = ["| " + " | ".join(head) + " |", "|" + "|".join([" --- "] * width) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def _body_font_size(pdf, sample_pages: int = 10) -> float:
    """Самый частый кегль в документе — точка отсчёта для заголовков."""
    sizes: collections.Counter = collections.Counter()
    for page in pdf.pages[:sample_pages]:
        try:
            for char in page.chars:
                sizes[round(char["size"], 1)] += 1
        except Exception:
            continue
    return sizes.most_common(1)[0][0] if sizes else 0.0


def _line_metrics(line: dict) -> tuple[float, float]:
    """(максимальный кегль строки, доля жирных символов)."""
    chars = line.get("chars", ())
    if not chars:
        return 0.0, 0.0
    max_size = max(round(c.get("size", 0), 1) for c in chars)
    bold = sum(1 for c in chars if "bold" in str(c.get("fontname", "")).lower())
    return max_size, bold / len(chars)


def _heading_level(line: dict, body_size: float) -> int:
    """0 — не заголовок, иначе уровень markdown (1 крупнее, 2 помельче)."""
    text = line.get("text", "").strip()
    if not text:
        return 0
    max_size, bold_ratio = _line_metrics(line)
    if body_size and max_size >= body_size * _HEADING_SIZE_RATIO:
        return 1 if max_size >= body_size * 1.4 else 2
    if (
        bold_ratio >= _BOLD_LINE_RATIO
        and len(text) <= _BOLD_HEADING_MAX_CHARS
        and not text.endswith((".", ";", ",", ":"))
    ):
        return 2
    return 0


def _line_to_markdown(line: dict, body_size: float) -> str:
    text = line.get("text", "").rstrip()
    if not text.strip():
        return ""
    level = _heading_level(line, body_size)
    if level:
        return "#" * level + " " + text.strip()
    marker = _LIST_MARKER_RE.match(text)
    if marker:
        return "- " + text[marker.end() :].strip()
    return text


def _column_split_x(page) -> float | None:
    """Абсцисса разделителя настоящей двухколоночной вёрстки, иначе None.

    Ищется сплошной вертикальный просвет: полоса, которую не пересекает ни
    одно слово, шире _COLUMN_GUTTER_MIN_PT и тянущаяся через бОльшую часть
    высоты страницы. Просто «средний разрыв по x» тут не годится — под него
    подходит любая таблица, а колонки таблицы читать раздельно нельзя.

    В корпусе компании двухколоночных страниц нет (проверено на 69-ФЗ,
    СП 1.13130, СП 4.13130 и положении НЛМК — 0 из 40 страниц), поэтому путь
    рассчитан на документы контрагентов и покрыт синтетическим тестом.
    """
    try:
        words = page.extract_words()
    except Exception:
        return None
    if len(words) < 2 * _COLUMN_MIN_WORDS_PER_SIDE:
        return None
    mid = page.width / 2
    left = [w for w in words if w["x1"] <= mid]
    right = [w for w in words if w["x0"] >= mid]
    if len(left) < _COLUMN_MIN_WORDS_PER_SIDE or len(right) < _COLUMN_MIN_WORDS_PER_SIDE:
        return None
    # Слова, пересекающие середину, ломают гипотезу о колонках.
    if any(w["x0"] < mid < w["x1"] for w in words):
        return None
    gutter = min(w["x0"] for w in right) - max(w["x1"] for w in left)
    if gutter < _COLUMN_GUTTER_MIN_PT:
        return None
    # Колонки должны идти через всю страницу, а не быть двумя блоками рядом.
    page_height = max(w["bottom"] for w in words) - min(w["top"] for w in words)
    if page_height <= 0:
        return None
    for side in (left, right):
        side_height = max(w["bottom"] for w in side) - min(w["top"] for w in side)
        if side_height / page_height < _COLUMN_MIN_HEIGHT_RATIO:
            return None
    return max(w["x1"] for w in left) + gutter / 2


def _plain_lines_text(page, body_size: float) -> str:
    try:
        lines = page.extract_text_lines()
    except Exception:
        return page.extract_text() or ""
    if not lines:
        return page.extract_text() or ""
    return "\n".join(_line_to_markdown(line, body_size) for line in lines)


def _columns_text(page, split_x: float, body_size: float) -> str:
    """Двухколоночная страница: сначала вся левая колонка, потом правая.

    Построчное чтение такой страницы склеивает начало строки левой колонки с
    началом строки правой, и текст превращается в чередование обрывков двух
    разных абзацев.
    """
    parts = []
    for x0, x1 in ((0, split_x), (split_x, page.width)):
        try:
            column = page.crop((x0, 0, x1, page.height))
        except Exception:
            continue
        text = _plain_lines_text(column, body_size)
        if text.strip():
            parts.append(text)
    return "\n".join(parts)


def _tables_text(page, tables: list, body_size: float) -> str:
    """Страница с рамочными таблицами: текст вне таблиц + markdown-таблицы.

    Порядок сохраняется по вертикали — таблица встаёт туда, где она стоит на
    странице, а не в конец, иначе подпись «Таблица 1 – Перечень ролей»
    оторвётся от своего содержимого.
    """
    # (позиция по вертикали, это_таблица, текст)
    blocks: list[tuple[float, bool, str]] = []
    boxes = []
    for table in tables:
        try:
            rows = table.extract()
        except Exception:
            continue
        markdown = _table_to_markdown(rows)
        if markdown:
            blocks.append((table.bbox[1], True, markdown))
            boxes.append(table.bbox)

    def outside_tables(obj) -> bool:
        cx = (obj["x0"] + obj["x1"]) / 2
        cy = (obj["top"] + obj["bottom"]) / 2
        return not any(x0 <= cx <= x1 and top <= cy <= bottom for x0, top, x1, bottom in boxes)

    try:
        rest = page.filter(outside_tables)
        for line in rest.extract_text_lines():
            markdown = _line_to_markdown(line, body_size)
            if markdown.strip():
                blocks.append((line["top"], False, markdown))
    except Exception as e:
        log.debug("Не удалось отделить текст от таблиц: %s", e)

    if not blocks:
        return page.extract_text() or ""
    blocks.sort(key=lambda b: b[0])

    # Подряд идущие строки текста — это абзац, склеиваем их обычным переносом.
    # Пустая строка отделяет только таблицу от текста вокруг неё, иначе весь
    # текст страницы уехал бы через строку и раздулся вдвое.
    out: list[str] = []
    previous_was_table = True
    for _, is_table, text in blocks:
        if is_table or previous_was_table:
            out.append(("\n\n" if out else "") + text)
        else:
            out.append("\n" + text)
        previous_was_table = is_table
    return "".join(out)


def _page_text(page, body_size: float = 0.0) -> str:
    tables = _bordered_tables(page)
    if tables:
        return _tables_text(page, tables, body_size)

    split_x = _column_split_x(page)
    if split_x is not None:
        return _columns_text(page, split_x, body_size)

    if _looks_columnar(page):
        text = page.extract_text(layout=True) or ""
        # layout=True добивает КАЖДУЮ строку пробелами до ширины страницы.
        # Отступы слева несут информацию о колонках и остаются, хвосты — чистый
        # расход контекста, режем их.
        return "\n".join(line.rstrip() for line in text.split("\n"))

    return _plain_lines_text(page, body_size)


def extract_pdf_pages(path: Path) -> list[str]:
    """Текстовый слой PDF ПОСТРАНИЧНО (пустая строка там, где слоя нет).

    Постранично, а не одной строкой, потому что смешанные PDF — обычное дело:
    договор набран в Word, а подписанные листы или приложения досняты сканом и
    подшиты в тот же файл. Вызывающий код (parsers/__init__.py) по этому списку
    решает для КАЖДОЙ страницы отдельно, брать текстовый слой или гнать её
    через OCR.
    """
    with pdfplumber.open(str(path)) as pdf:
        body_size = _body_font_size(pdf)
        return [_page_text(page, body_size) for page in pdf.pages]


def extract_pdf(path: Path) -> str:
    """Весь текстовый слой PDF одной строкой. Пустая строка, если это скан."""
    return "\n".join(t for t in extract_pdf_pages(path) if t.strip())
