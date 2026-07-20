"""Генерация .eml — готовое письмо для почтовой программы.

Открывается двойным кликом в Outlook/Thunderbird/Почте Windows как черновик:
адресат, тема и текст сопроводительного e-mail уже заполнены, DOCX на бланке
приложен. Пользователю остаётся нажать «Отправить».
"""

from __future__ import annotations

from email.message import EmailMessage
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_SENDER = "ООО «ПожСервис» <info@pozhservis48.ru>"
_DOCX_MIME = ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document")


def build_letter_eml(letter: dict, docx_path: Path | None, output_path: Path) -> Path:
    email_part = letter.get("email") or {}

    msg = EmailMessage()
    msg["From"] = _SENDER
    msg["To"] = email_part.get("кому") or ""
    msg["Subject"] = email_part.get("тема") or letter.get("тема") or "Письмо ООО «ПожСервис»"
    # X-Unsent: 1 — Outlook открывает файл как ЧЕРНОВИК для отправки,
    # а не как полученное письмо.
    msg["X-Unsent"] = "1"
    msg.set_content(email_part.get("тело") or letter.get("тело") or "")

    if docx_path is not None and docx_path.exists():
        maintype, subtype = _DOCX_MIME
        msg.add_attachment(
            docx_path.read_bytes(),
            maintype=maintype,
            subtype=subtype,
            filename=docx_path.name,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(bytes(msg))
    return output_path
