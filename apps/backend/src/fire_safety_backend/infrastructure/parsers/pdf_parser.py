from pathlib import Path

import pdfplumber


def extract_pdf(path: Path) -> str:
    """Извлекает текстовый слой PDF. Возвращает пустую строку, если это скан."""
    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text)
    return "\n".join(parts)
