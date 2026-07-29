"""OCR для сканов: EasyOCR, если он установлен, иначе Tesseract.

Импорты pytesseract/PIL/pdf2image/easyocr — ленивые. На dev-машине без
OCR-стека модуль импортируется, но при вызове функций поднимает понятную
ошибку.

Почему EasyOCR предпочтительнее там, где он есть: он отдаёт уверенность по
каждому распознанному фрагменту, и фрагменты ниже порога заменяются на `[?]`.
Для юр. анализа это принципиально — распознанный скан попадает в цитаты
договора, и явная дыра честнее правдоподобной подделки: на реальном скане
при средней уверенности ~80 Tesseract выдавал «г. Линецк» вместо «г. Липецк»
и «именусмое» вместо «именуемое», а модель цитировала это как текст договора.

Уверенность приводится к общей шкале 0..100: Tesseract отдаёт проценты,
EasyOCR — доли единицы.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ... import config

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)


class OCRNotAvailable(RuntimeError):
    pass


_easyocr_reader: object | None = None
_easyocr_unavailable = False


def _load_easyocr():
    """Готовый easyocr.Reader либо None, если пакет не установлен/отключён.

    Reader создаётся один раз и переиспользуется: он поднимает модели в
    память, и делать это на каждую страницу — секунды впустую.
    """
    global _easyocr_reader, _easyocr_unavailable
    if not config.USE_EASYOCR or _easyocr_unavailable:
        return None
    if _easyocr_reader is not None:
        return _easyocr_reader
    try:
        import easyocr
    except ImportError:
        _easyocr_unavailable = True
        log.info("easyocr не установлен — OCR идёт через Tesseract")
        return None
    try:
        _easyocr_reader = easyocr.Reader(list(config.EASYOCR_LANGS), gpu=False)
    except Exception as e:  # noqa: BLE001 — модели могут не скачаться офлайн
        _easyocr_unavailable = True
        log.warning("easyocr не поднялся (%s) — откатываюсь на Tesseract", e)
        return None
    return _easyocr_reader


def _easyocr_read(image) -> tuple[str, float | None]:
    """Распознаёт изображение через EasyOCR. Возвращает (текст, уверенность 0..100).

    Фрагменты с уверенностью ниже config.EASYOCR_MIN_CONFIDENCE заменяются на
    `[?]`. EasyOCR отдаёт уверенность на распознанную ОБЛАСТЬ (обычно строка
    или её часть), а не на отдельное слово, поэтому и заменяется область
    целиком — придумывать разбиение на слова с непонятной уверенностью хуже,
    чем честно пометить весь фрагмент.
    """
    reader = _load_easyocr()
    if reader is None:
        raise OCRNotAvailable("easyocr недоступен")
    import numpy as np

    result = reader.readtext(np.array(image), detail=1, paragraph=False)
    parts: list[str] = []
    confidences: list[float] = []
    for item in result:
        # (bbox, text, confidence)
        if len(item) < 3:
            continue
        text, confidence = str(item[1]), float(item[2])
        confidences.append(confidence * 100)
        parts.append(text if confidence >= config.EASYOCR_MIN_CONFIDENCE else "[?]")
    if not parts:
        return "", None
    mean = round(sum(confidences) / len(confidences), 1)
    return " ".join(parts), mean


def _read_image(image) -> tuple[str, float | None]:
    """Единая точка распознавания: EasyOCR, если доступен, иначе Tesseract."""
    if _load_easyocr() is not None:
        try:
            return _easyocr_read(image)
        except Exception as e:  # noqa: BLE001
            log.warning("EasyOCR не справился (%s) — эта страница через Tesseract", e)
    pytesseract, _ = _load_tesseract()
    return pytesseract.image_to_string(image, lang=config.TESSERACT_LANG), ocr_confidence(image)


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


def _open_image(path: Path):
    try:
        from PIL import Image
    except ImportError as e:
        raise OCRNotAvailable("Pillow не установлен: pip install Pillow") from e
    return Image.open(str(path))


def ocr_image_with_confidence(path: Path) -> tuple[str, float | None]:
    with _open_image(path) as img:
        return _read_image(img)


def ocr_image(path: Path) -> str:
    return ocr_image_with_confidence(path)[0]


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
    pages = _convert_from_path(path, first_page=page_number, last_page=page_number)
    if not pages:
        return "", None
    return _read_image(pages[0])


def ocr_pdf_page(path: Path, page_number: int) -> str:
    return ocr_pdf_page_with_confidence(path, page_number)[0]


def ocr_pdf_with_confidence(path: Path) -> tuple[str, float | None]:
    """Рендерит ВСЕ страницы PDF в изображения и прогоняет через Tesseract.

    Быстрый путь для файлов, где текстового слоя нет вовсе: poppler
    запускается один раз на весь документ, а не на каждую страницу.
    Требует установленный poppler (для pdf2image).
    """
    pages = _convert_from_path(path)
    parts: list[str] = []
    confs: list[float] = []
    for i, img in enumerate(pages, start=1):
        text, conf = _read_image(img)
        if conf is not None:
            confs.append(conf)
        if text.strip():
            parts.append(f"--- Стр. {i} ---\n{text}")
    mean_conf = round(sum(confs) / len(confs), 1) if confs else None
    return "\n".join(parts), mean_conf


def ocr_pdf(path: Path) -> str:
    return ocr_pdf_with_confidence(path)[0]
