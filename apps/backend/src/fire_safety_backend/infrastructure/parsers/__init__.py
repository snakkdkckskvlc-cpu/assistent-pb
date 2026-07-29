import logging
from dataclasses import dataclass, field
from pathlib import Path

from .docx_parser import extract_docx
from .ocr import (
    ocr_image,
    ocr_image_with_confidence,
    ocr_pdf,
    ocr_pdf_page,
    ocr_pdf_page_with_confidence,
    ocr_pdf_with_confidence,
)
from .pdf_parser import extract_pdf, extract_pdf_pages

log = logging.getLogger(__name__)

# Ниже этой средней уверенности Tesseract текст стоит читать с подозрением:
# на реальном скане договора при ~80 попадались «г. Линецк» вместо «Липецк»
# и «именусмое» вместо «именуемое».
_LOW_CONFIDENCE = 75.0


@dataclass
class ExtractionMeta:
    """Как именно был получен текст — чтобы интерфейс мог честно предупредить.

    Для юр. анализа это важнее, чем кажется: цитата из договора, поднятая с
    распознанного скана, может содержать опечатку распознавания, и
    пользователь должен понимать, почему цитата не совпадает с бумагой.
    """

    total_pages: int = 0
    ocr_pages: list[int] = field(default_factory=list)
    ocr_confidence: float | None = None

    @property
    def used_ocr(self) -> bool:
        return bool(self.ocr_pages)

    @property
    def warning(self) -> str:
        if not self.used_ocr:
            return ""
        if len(self.ocr_pages) == self.total_pages:
            where = "Документ распознан со скана"
        else:
            pages = ", ".join(str(p) for p in self.ocr_pages[:10])
            if len(self.ocr_pages) > 10:
                pages += " и др."
            where = f"Часть страниц распознана со скана (стр. {pages})"
        if self.ocr_confidence is None:
            return f"{where} — возможны ошибки распознавания."
        quality = "низкое" if self.ocr_confidence < _LOW_CONFIDENCE else "приемлемое"
        return (
            f"{where}. Качество распознавания {quality} "
            f"({self.ocr_confidence:.0f}%) — возможны ошибки в тексте и цитатах."
        )


# Меньше этого числа символов на странице — считаем, что осмысленного
# текстового слоя нет. Не ноль, потому что сканеры и «печать в PDF» часто
# оставляют на странице-картинке крохи текста: колонтитул, номер страницы,
# «Scanned by ...». По прежней логике («есть хоть что-то — берём») такая
# страница считалась текстовой, и её реальное содержимое терялось.
_MIN_TEXT_CHARS_PER_PAGE = 50


class UnsupportedFormatError(ValueError):
    pass


def _extract_pdf_with_ocr_fallback(path: Path, meta: ExtractionMeta | None = None) -> str:
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
    if meta is not None:
        meta.total_pages = len(page_texts)

    scan_pages = [
        i for i, t in enumerate(page_texts, start=1) if len(t.strip()) < _MIN_TEXT_CHARS_PER_PAGE
    ]

    # Текстового слоя нет нигде — обычный скан. Один прогон poppler на весь
    # файл вместо постраничных запусков.
    if len(scan_pages) == len(page_texts):
        text, conf = ocr_pdf_with_confidence(path)
        if meta is not None:
            meta.ocr_pages = scan_pages
            meta.ocr_confidence = conf
        return text

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
    confs: list[float] = []
    recognized: list[int] = []
    for i, text in enumerate(page_texts, start=1):
        if i not in scan_pages:
            parts.append(text)
            continue
        try:
            ocr_text, conf = ocr_pdf_page_with_confidence(path, i)
        except Exception as e:
            # OCR может быть недоступен (нет Tesseract) — тогда честно
            # помечаем дыру в тексте, а не делаем вид, что страницы не было.
            log.warning("PDF %s: не удалось распознать стр. %d: %s", path.name, i, e)
            parts.append(f"--- Стр. {i}: не удалось распознать ---")
            continue
        if conf is not None:
            confs.append(conf)
        if ocr_text.strip():
            recognized.append(i)
            parts.append(f"--- Стр. {i} (распознано) ---\n{ocr_text}")
    if meta is not None:
        meta.ocr_pages = recognized
        meta.ocr_confidence = round(sum(confs) / len(confs), 1) if confs else None
    return "\n".join(parts)


def extract_text_with_meta(path: Path) -> tuple[str, ExtractionMeta]:
    """extract_text + информация о том, откуда взялся текст (OCR и его качество)."""
    meta = ExtractionMeta()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf_with_ocr_fallback(path, meta), meta
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        meta.total_pages = 1
        meta.ocr_pages = [1]
        return ocr_image(path), meta
    return extract_text(path), meta


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
    "ExtractionMeta",
    "UnsupportedFormatError",
    "extract_docx",
    "extract_pdf",
    "extract_pdf_pages",
    "extract_text",
    "extract_text_with_meta",
    "ocr_image",
    "ocr_pdf",
    "ocr_image_with_confidence",
    "ocr_pdf_page",
]
