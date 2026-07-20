"""Юнит-тесты генератора .eml (infrastructure/generators/letter_eml.py)."""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path

from fire_safety_backend.infrastructure.generators.letter_eml import build_letter_eml

_LETTER = {
    "тема": "О проведении ревизии",
    "тело": "Текст официального письма.",
    "email": {
        "кому": "yarikov@mss.ru",
        "тема": "Письмо ООО «ПожСервис» — О проведении ревизии",
        "тело": "Здравствуйте!\n\nНаправляем письмо. Оригинал во вложении.",
    },
}


def _parse(path: Path):
    return BytesParser(policy=policy.default).parsebytes(path.read_bytes())


def test_eml_roundtrip_with_attachment(tmp_path: Path) -> None:
    docx = tmp_path / "letter.docx"
    docx.write_bytes(b"fake docx bytes")
    out = tmp_path / "letter.eml"

    build_letter_eml(_LETTER, docx, out)
    msg = _parse(out)

    assert msg["To"] == "yarikov@mss.ru"
    assert "О проведении ревизии" in msg["Subject"]
    assert msg["X-Unsent"] == "1"  # Outlook открывает как черновик
    assert "Направляем письмо" in msg.get_body(("plain",)).get_content()

    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "letter.docx"
    assert attachments[0].get_payload(decode=True) == b"fake docx bytes"


def test_eml_without_docx_has_no_attachment(tmp_path: Path) -> None:
    out = tmp_path / "letter.eml"
    build_letter_eml(_LETTER, None, out)
    msg = _parse(out)
    assert list(msg.iter_attachments()) == []
    assert msg["To"] == "yarikov@mss.ru"


def test_eml_empty_email_block_falls_back_to_letter_fields(tmp_path: Path) -> None:
    letter = {"тема": "Тема письма", "тело": "Тело письма."}
    out = tmp_path / "letter.eml"
    build_letter_eml(letter, None, out)
    msg = _parse(out)
    assert msg["Subject"] == "Тема письма"
    assert "Тело письма." in msg.get_body(("plain",)).get_content()
