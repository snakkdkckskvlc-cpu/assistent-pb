"""Извлечение текстового слоя PDF, постранично.

Страницы с таблицами рендерятся с сохранением раскладки (layout=True), всё
остальное — обычным потоком текста. Причина: у pdfplumber обычный
`extract_text()` отдаёт слова в порядке следования в PDF, а не по колонкам,
и в таблицах значение регулярно оказывается ПЕРЕД своей строкой:

    1,3     1,13
    БУР/ЕР 2022
    1,703   1,1413
    БУР 2021

— модель уверенно припишет коэффициент не тому справочнику. `layout=True`
сохраняет колонки и связь «строка ↔ значение» становится читаемой.

Включать layout везде нельзя: он раздувает текст в 2.6–6 раз (17-страничный
договор: ~12k → ~32k токенов), а контекст модели 8k — это втрое больше
чанков и втрое дольше юр. анализ. Поэтому layout применяется только к
страницам, которые реально похожи на таблицу.
"""

from pathlib import Path

import pdfplumber

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


def _has_bordered_table(page) -> bool:
    try:
        tables = page.find_tables()
    except Exception:
        return False
    return any(len(t.rows) >= _MIN_TABLE_ROWS and len(t.columns) >= _MIN_TABLE_COLS for t in tables)


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


def _page_text(page) -> str:
    if not (_has_bordered_table(page) or _looks_columnar(page)):
        return page.extract_text() or ""
    text = page.extract_text(layout=True) or ""
    # layout=True добивает КАЖДУЮ строку пробелами до ширины страницы.
    # Отступы слева несут информацию о колонках и остаются, хвосты — чистый
    # расход контекста, режем их.
    return "\n".join(line.rstrip() for line in text.split("\n"))


def extract_pdf_pages(path: Path) -> list[str]:
    """Текстовый слой PDF ПОСТРАНИЧНО (пустая строка там, где слоя нет).

    Постранично, а не одной строкой, потому что смешанные PDF — обычное дело:
    договор набран в Word, а подписанные листы или приложения досняты сканом и
    подшиты в тот же файл. Вызывающий код (parsers/__init__.py) по этому списку
    решает для КАЖДОЙ страницы отдельно, брать текстовый слой или гнать её
    через OCR.
    """
    with pdfplumber.open(str(path)) as pdf:
        return [_page_text(page) for page in pdf.pages]


def extract_pdf(path: Path) -> str:
    """Весь текстовый слой PDF одной строкой. Пустая строка, если это скан."""
    return "\n".join(t for t in extract_pdf_pages(path) if t.strip())
