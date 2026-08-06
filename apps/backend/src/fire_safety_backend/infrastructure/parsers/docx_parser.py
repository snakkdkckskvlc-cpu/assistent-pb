"""Чтение DOCX: текст абзацев и таблиц в порядке документа.

### Почему не `paragraph.text`

`python-docx` собирает `p.text` только из прямых потомков `w:p` типа `w:r`. Всё,
что лежит глубже, туда не попадает — а именно там живут ПРАВКИ РЕЦЕНЗИРОВАНИЯ:

* вставленный текст лежит в `w:ins`, и его прогоны детьми абзаца не являются;
* удалённый текст лежит в `w:delText`, а не в `w:t`.

Проверено экспериментально: в «в течение 30 банковских дней» число ИСЧЕЗАЛО
(оставался двойной пробел), а удалённый пункт про неустойку не был виден вовсе.

Договор от контрагента с правками — норма при согласовании, а не редкость. И
модель получала текст с дырами, не зная, что они есть: это худший вид отказа,
потому что ответ выглядит полным.

### Почему обход идёт по телу документа

Раньше абзацы и таблицы выгружались раздельно: сначала все абзацы, потом все
таблицы. Таблица уезжала в конец и отрывалась от пункта, который на неё
ссылается («порядок оплаты приведён в таблице»), — а для разбора договора связь
пункта с таблицей и есть содержание.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

if TYPE_CHECKING:
    from pathlib import Path

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# w:t — обычный текст, w:delText — удалённый при рецензировании. Оба нужны:
# зачёркнутый в согласовании пункт для разбора рисков не менее важен, чем
# оставшийся, а иногда важнее.
_TEXT_TAGS = (f"{_W}t", f"{_W}delText")


def paragraph_text(paragraph: Paragraph) -> str:
    """Весь текст абзаца, включая правки рецензирования.

    Обходится всё поддерево, а не прямые потомки: вставленный текст лежит
    внутри `w:ins`, и `paragraph.text` его не видит.
    """
    return "".join(node.text or "" for node in paragraph._p.iter() if node.tag in _TEXT_TAGS)


def _table_text(table: Table) -> list[str]:
    rows: list[str] = []
    for row in table.rows:
        cells = [" ".join(paragraph_text(p) for p in cell.paragraphs).strip() for cell in row.cells]
        line = " | ".join(c for c in cells if c)
        if line:
            rows.append(line)
    return rows


def extract_docx(path: Path) -> str:
    """Текст DOCX в порядке документа: абзацы и таблицы вперемешку, как в файле."""
    doc = Document(str(path))
    parts: list[str] = []
    for child in doc.element.body.iterchildren():
        if child.tag == f"{_W}p":
            text = paragraph_text(Paragraph(child, doc)).strip()
            if text:
                parts.append(text)
        elif child.tag == f"{_W}tbl":
            parts.extend(_table_text(Table(child, doc)))
    return "\n".join(parts)
