from pathlib import Path

from .docx_parser import extract_docx
from .pdf_parser import extract_pdf
from .ocr import ocr_image, ocr_pdf


class UnsupportedFormatError(ValueError):
    pass


def extract_text(path: Path) -> str:
    """Универсальный экстрактор — выбирает парсер по расширению.

    Для PDF: если текстовый слой есть — pdfplumber, иначе OCR.
    """
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".pdf":
        text = extract_pdf(path)
        if text.strip():
            return text
        # Пустой текстовый слой → скан
        return ocr_pdf(path)
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        return ocr_image(path)
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    raise UnsupportedFormatError(f"Формат не поддерживается: {suffix}")


__all__ = ["extract_text", "extract_docx", "extract_pdf", "ocr_image", "ocr_pdf", "UnsupportedFormatError"]
