"""Генерация DOCX-письма поверх фирменного бланка.

Ожидает, что в шаблоне letterhead.docx есть плейсхолдеры:
  {{date}}, {{recipient}}, {{subject}}, {{greeting}}, {{body}},
  {{signoff}}, {{sender_position}}, {{sender_name}}

Многострочные значения ({{recipient}} = «Директору …\\nИванову А.А.»,
многоабзацное {{body}}) корректно разбиваются на параграфы с сохранением
формата исходного плейсхолдер-параграфа.

Если шаблона нет — генерирует чистый DOCX без бланка.
"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import date
from typing import TYPE_CHECKING

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from ... import config

if TYPE_CHECKING:
    from pathlib import Path

_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def _substitute(text: str, mapping: dict[str, str]) -> str | None:
    """Заменяет все плейсхолдеры в исходном тексте параграфа за один проход.

    Один re.sub по оригинальному тексту (а не последовательные .replace()
    по ключам) исключает повторную подстановку: значение одного ключа не
    может случайно содержать «{{другой_ключ}}» и быть заменено ещё раз —
    re.sub никогда не пересканирует уже подставленный текст.
    Возвращает None, если в тексте не было ни одного плейсхолдера из mapping
    (сигнал вызывающему коду ничего не менять).
    """
    found = False

    def _repl(m: re.Match[str]) -> str:
        nonlocal found
        key = m.group(1)
        if key not in mapping:
            return m.group(0)
        found = True
        return str(mapping[key]) if mapping[key] else ""

    new_text = _PLACEHOLDER_RE.sub(_repl, text)
    return new_text if found else None


def _replace_in_paragraph(paragraph, mapping: dict[str, str]) -> None:
    new_text = _substitute(paragraph.text, mapping)
    if new_text is None:
        return

    lines = new_text.split("\n") if new_text else [""]
    first_line = lines[0]

    # Пересобираем runs так, чтобы сохранить формат первого run
    for run in paragraph.runs:
        run.text = ""
    if paragraph.runs:
        paragraph.runs[0].text = first_line
    else:
        paragraph.add_run(first_line)

    # Остальные строки — как отдельные параграфы сразу после текущего,
    # с копией формата (стили, выравнивание, шрифт).
    prev_p = paragraph
    for line in lines[1:]:
        new_p_xml = deepcopy(paragraph._p)
        prev_p._p.addnext(new_p_xml)
        # Нужен объект Paragraph, а не только XML — получим через parent
        from docx.text.paragraph import Paragraph as _P

        new_p = _P(new_p_xml, paragraph._parent)
        for r in new_p.runs:
            r.text = ""
        if new_p.runs:
            new_p.runs[0].text = line
        else:
            new_p.add_run(line)
        prev_p = new_p


def _apply_mapping(doc: Document, mapping: dict[str, str]) -> None:
    for p in doc.paragraphs:
        _replace_in_paragraph(p, mapping)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    _replace_in_paragraph(p, mapping)


def build_letter_docx(letter: dict, output_path: Path) -> Path:
    """Собирает DOCX-письмо. Если фирменный бланк есть — использует его."""
    mapping = {
        "date": date.today().strftime("%d.%m.%Y"),
        "recipient": letter.get("получатель", ""),
        "subject": letter.get("тема", ""),
        "greeting": letter.get("обращение", ""),
        "body": letter.get("тело", ""),
        "signoff": letter.get("формула_вежливости", "С уважением,"),
        "sender_position": letter.get("должность_отправителя_placeholder", "[должность]"),
        "sender_name": letter.get("фио_отправителя_placeholder", "[Фамилия И.О.]"),
    }

    if config.LETTERHEAD_TEMPLATE.exists():
        doc = Document(str(config.LETTERHEAD_TEMPLATE))
        _apply_mapping(doc, mapping)
    else:
        # Fallback — минимальный документ
        doc = Document()
        doc.add_paragraph(mapping["date"]).alignment = WD_ALIGN_PARAGRAPH.RIGHT
        doc.add_paragraph()
        if mapping["recipient"]:
            doc.add_paragraph(mapping["recipient"])
        if mapping["subject"]:
            p = doc.add_paragraph()
            p.add_run(f"Касательно: {mapping['subject']}").bold = True
        doc.add_paragraph()
        doc.add_paragraph(mapping["greeting"])
        doc.add_paragraph()
        for para in mapping["body"].split("\n"):
            if para.strip():
                doc.add_paragraph(para.strip())
        doc.add_paragraph()
        doc.add_paragraph(mapping["signoff"])
        doc.add_paragraph(mapping["sender_position"])
        doc.add_paragraph(mapping["sender_name"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path
