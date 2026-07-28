"""OCR для сканов через Tesseract.

Импорты pytesseract/PIL/pdf2image — ленивые. На dev-машине без OCR-стека
модуль импортируется, но при вызове функций поднимает понятную ошибку.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ... import config

if TYPE_CHECKING:
    from pathlib import Path


class OCRNotAvailable(RuntimeError):
    pass


def _load_tesseract():
    try:
        import pytesseract
        from PIL import Image
    except ImportError as e:
        raise OCRNotAvailable(
            "Tesseract/Pillow не установлены. Для OCR-сканов установите: "
            "pip install pytesseract Pillow pdf2image"
        ) from e
    if config.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD
    return pytesseract, Image


def ocr_image(path: Path) -> str:
    pytesseract, Image = _load_tesseract()
    with Image.open(str(path)) as img:
        return pytesseract.image_to_string(img, lang=config.TESSERACT_LANG)


def _convert_from_path(path: Path, *, first_page: int | None = None, last_page: int | None = None):
    try:
        from pdf2image import convert_from_path
    except ImportError as e:
        raise OCRNotAvailable("pdf2image не установлен") from e
    return convert_from_path(str(path), dpi=250, first_page=first_page, last_page=last_page)


def ocr_confidence(image) -> float | None:
    """Средняя уверенность Tesseract по словам страницы (0..100).

    `image_to_data` отдаёт confidence по каждому слову — это бесплатно (тот
    же вызов Tesseract, только другой output_type) и позволяет честно
    сказать «скан плохого качества» вместо тихой выдачи текста с ошибками
    вида «г. Линецк» вместо «г. Липецк».
    """
    pytesseract, _ = _load_tesseract()
    try:
        from pytesseract import Output

        data = pytesseract.image_to_data(image, lang=config.TESSERACT_LANG, output_type=Output.DICT)
    except Exception:
        return None
    confs = [
        float(c) for c in data.get("conf", []) if str(c).lstrip("-").isdigit() and float(c) >= 0
    ]
    return round(sum(confs) / len(confs), 1) if confs else None


def ocr_pdf_page_with_confidence(path: Path, page_number: int) -> tuple[str, float | None]:
    """OCR ОДНОЙ страницы PDF (нумерация с 1) + средняя уверенность.

    Нужен для смешанных PDF, где часть страниц набрана текстом, а часть —
    сканы: гнать через OCR весь файл ради двух страниц незачем (OCR ~2 сек
    на страницу против миллисекунд на чтение готового текстового слоя).
    """
    pytesseract, _ = _load_tesseract()
    pages = _convert_from_path(path, first_page=page_number, last_page=page_number)
    if not pages:
        return "", None
    text = pytesseract.image_to_string(pages[0], lang=config.TESSERACT_LANG)
    return text, ocr_confidence(pages[0])


def ocr_pdf_page(path: Path, page_number: int) -> str:
    return ocr_pdf_page_with_confidence(path, page_number)[0]


def ocr_pdf_with_confidence(path: Path) -> tuple[str, float | None]:
    """Рендерит ВСЕ страницы PDF в изображения и прогоняет через Tesseract.

    Быстрый путь для файлов, где текстового слоя нет вовсе: poppler
    запускается один раз на весь документ, а не на каждую страницу.
    Требует установленный poppler (для pdf2image).
    """
    pytesseract, _ = _load_tesseract()
    pages = _convert_from_path(path)
    parts: list[str] = []
    confs: list[float] = []
    for i, img in enumerate(pages, start=1):
        text = pytesseract.image_to_string(img, lang=config.TESSERACT_LANG)
        conf = ocr_confidence(img)
        if conf is not None:
            confs.append(conf)
        if text.strip():
            parts.append(f"--- Стр. {i} ---\n{text}")
    mean_conf = round(sum(confs) / len(confs), 1) if confs else None
    return "\n".join(parts), mean_conf


def ocr_pdf(path: Path) -> str:
    return ocr_pdf_with_confidence(path)[0]
