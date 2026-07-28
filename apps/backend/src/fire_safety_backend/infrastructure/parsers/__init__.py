import logging
from pathlib import Path

from .docx_parser import extract_docx
from .ocr import ocr_image, ocr_pdf, ocr_pdf_page
from .pdf_parser import extract_pdf, extract_pdf_pages

log = logging.getLogger(__name__)

# Меньше этого числа символов на странице — считаем, что осмысленного
# текстового слоя нет. Не ноль, потому что сканеры и «печать в PDF» часто
# оставляют на странице-картинке крохи текста: колонтитул, номер страницы,
# «Scanned by ...». По прежней логике («есть хоть что-то — берём») такая
# страница считалась текстовой, и её реальное содержимое терялось.
_MIN_TEXT_CHARS_PER_PAGE = 50


class UnsupportedFormatError(ValueError):
    pass


def _extract_pdf_with_ocr_fallback(path: Path) -> str:
    """Постранично: где есть текстовый слой — берём его, где нет — OCR.

    Раньше решение принималось на весь документ разом («если извлёкся хоть
    какой-то текст — возвращаем его, иначе OCR»). На смешанных PDF это молча
    теряло содержимое: договор набран в Word, подписанные листы или приложения
    досняты сканом — возвращались только текстовые страницы, а сканы
    выбрасывались без единого предупреждения. Для юр. анализа это опаснее
    явной ошибки: модель уверенно отвечает «штрафных санкций не найдено» по
    куску договора, не зная, что половина документа до неё не доехала.
    """
    page_texts = extract_pdf_pages(path)
    if not page_texts:
        return ""

    scan_pages = [
        i for i, t in enumerate(page_texts, start=1) if len(t.strip()) < _MIN_TEXT_CHARS_PER_PAGE
    ]

    # Текстового слоя нет нигде — обычный скан. Один прогон poppler на весь
    # файл вместо постраничных запусков.
    if len(scan_pages) == len(page_texts):
        return ocr_pdf(path)

    if not scan_pages:
        return "\n".join(page_texts)

    log.info(
        "PDF %s: смешанный — текстовых страниц %d, страниц-сканов %d (уходят в OCR: %s)",
        path.name,
        len(page_texts) - len(scan_pages),
        len(scan_pages),
        scan_pages,
    )
    parts: list[str] = []
    for i, text in enumerate(page_texts, start=1):
        if i not in scan_pages:
            parts.append(text)
            continue
        try:
            ocr_text = ocr_pdf_page(path, i)
        except Exception as e:
            # OCR может быть недоступен (нет Tesseract) — тогда честно
            # помечаем дыру в тексте, а не делаем вид, что страницы не было.
            log.warning("PDF %s: не удалось распознать стр. %d: %s", path.name, i, e)
            parts.append(f"--- Стр. {i}: не удалось распознать ---")
            continue
        if ocr_text.strip():
            parts.append(f"--- Стр. {i} (распознано) ---\n{ocr_text}")
    return "\n".join(parts)


def extract_text(path: Path) -> str:
    """Универсальный экстрактор — выбирает парсер по расширению.

    Для PDF: постранично — текстовый слой там, где он есть, OCR там, где нет.
    """
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".pdf":
        return _extract_pdf_with_ocr_fallback(path)
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        return ocr_image(path)
    if suffix == ".txt":
        return path.read_text(encoding="utf-8", errors="replace")
    raise UnsupportedFormatError(f"Формат не поддерживается: {suffix}")


__all__ = [
    "UnsupportedFormatError",
    "extract_docx",
    "extract_pdf",
    "extract_pdf_pages",
    "extract_text",
    "ocr_image",
    "ocr_pdf",
    "ocr_pdf_page",
]
