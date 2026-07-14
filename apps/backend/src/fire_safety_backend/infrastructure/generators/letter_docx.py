"""Генерация DOCX-письма поверх фирменного бланка.

Ожидает, что в шаблоне letterhead.docx есть плейсхолдеры:
  {{date}}, {{recipient}}, {{subject}}, {{greeting}}, {{body}},
  {{signoff}}, {{sender_position}}, {{sender_name}}

Если шаблона нет — генерирует чистый DOCX без бланка.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from docx import Document

from ... import config


def _replace_in_paragraph(paragraph, mapping: dict[str, str]) -> None:
    for key, value in mapping.items():
        placeholder = f"{{{{{key}}}}}"
        if placeholder in paragraph.text:
            # Пересобираем runs, чтобы не портить форматирование сильно
            full = paragraph.text.replace(placeholder, value)
            for run in paragraph.runs:
                run.text = ""
            if paragraph.runs:
                paragraph.runs[0].text = full
            else:
                paragraph.add_run(full)


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
        doc.add_paragraph(mapping["date"]).alignment = 2  # right
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
