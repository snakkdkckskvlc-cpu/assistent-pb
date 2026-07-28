from pathlib import Path

import pdfplumber


def extract_pdf_pages(path: Path) -> list[str]:
    """Текстовый слой PDF ПОСТРАНИЧНО (пустая строка там, где слоя нет).

    Постранично, а не одной строкой, потому что смешанные PDF — обычное дело:
    договор набран в Word, а подписанные листы или приложения досняты сканом и
    подшиты в тот же файл. Вызывающий код (parsers/__init__.py) по этому списку
    решает для КАЖДОЙ страницы отдельно, брать текстовый слой или гнать её
    через OCR.
    """
    with pdfplumber.open(str(path)) as pdf:
        return [(page.extract_text() or "") for page in pdf.pages]


def extract_pdf(path: Path) -> str:
    """Весь текстовый слой PDF одной строкой. Пустая строка, если это скан."""
    return "\n".join(t for t in extract_pdf_pages(path) if t.strip())
