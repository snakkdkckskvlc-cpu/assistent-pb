"""Юнит-тесты выбора «текстовый слой vs OCR» для PDF.

Tesseract/poppler в CI нет, поэтому OCR-слой мокается — проверяется именно
логика РЕШЕНИЯ (какие страницы уходят в OCR, а какие берутся из текстового
слоя), а не качество распознавания.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fire_safety_backend.infrastructure import parsers


def _mock_pages(monkeypatch: pytest.MonkeyPatch, page_texts: list[str]) -> None:
    monkeypatch.setattr(parsers, "extract_pdf_pages", lambda path: page_texts)


def test_mixed_pdf_ocrs_only_scan_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Регрессия: договор набран в Word, подписанные листы досняты сканом.

    Прежняя логика («извлёкся хоть какой-то текст — возвращаем его») молча
    выбрасывала страницы-сканы, и юр. анализ шёл по огрызку договора.
    """
    _mock_pages(monkeypatch, ["А" * 200, "", "Б" * 200])
    ocr_calls: list[int] = []

    def fake_ocr_page(path: Path, page_number: int) -> str:
        ocr_calls.append(page_number)
        return "распознанный текст страницы 2"

    monkeypatch.setattr(parsers, "ocr_pdf_page", fake_ocr_page)

    result = parsers._extract_pdf_with_ocr_fallback(Path("dummy.pdf"))

    assert ocr_calls == [2], "OCR должен запускаться ровно на странице-скане"
    assert "А" * 200 in result
    assert "Б" * 200 in result
    assert "распознанный текст страницы 2" in result


def test_junk_text_layer_page_treated_as_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сканеры оставляют на странице-картинке крохи текста («Scanned by …»,
    номер страницы). Такая страница обязана уйти в OCR, а не считаться
    текстовой из-за пары символов."""
    _mock_pages(monkeypatch, ["В" * 300, "Scanned by CamScanner"])
    ocr_calls: list[int] = []

    def fake_ocr_page(path: Path, page_number: int) -> str:
        ocr_calls.append(page_number)
        return "реальное содержимое страницы"

    monkeypatch.setattr(parsers, "ocr_pdf_page", fake_ocr_page)

    result = parsers._extract_pdf_with_ocr_fallback(Path("dummy.pdf"))

    assert ocr_calls == [2]
    assert "реальное содержимое страницы" in result


def test_fully_digital_pdf_never_calls_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_pages(monkeypatch, ["Г" * 300, "Д" * 300])

    def boom(*args, **kwargs):
        raise AssertionError("OCR не должен вызываться для цифрового PDF")

    monkeypatch.setattr(parsers, "ocr_pdf_page", boom)
    monkeypatch.setattr(parsers, "ocr_pdf", boom)

    result = parsers._extract_pdf_with_ocr_fallback(Path("dummy.pdf"))
    assert result == "Г" * 300 + "\n" + "Д" * 300


def test_fully_scanned_pdf_uses_bulk_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Скан целиком — один прогон poppler на весь файл, а не постранично
    (постранично он перезапускался бы на каждую страницу)."""
    _mock_pages(monkeypatch, ["", "", ""])
    monkeypatch.setattr(parsers, "ocr_pdf", lambda path: "весь документ распознан")

    def boom(*args, **kwargs):
        raise AssertionError("для полного скана должен использоваться bulk-OCR")

    monkeypatch.setattr(parsers, "ocr_pdf_page", boom)

    assert parsers._extract_pdf_with_ocr_fallback(Path("dummy.pdf")) == "весь документ распознан"


def test_ocr_failure_marks_gap_instead_of_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Нет Tesseract — текстовые страницы всё равно должны вернуться, а на
    месте нераспознанной должна остаться явная пометка, а не тишина."""
    _mock_pages(monkeypatch, ["Е" * 300, ""])

    def failing_ocr(path: Path, page_number: int) -> str:
        raise RuntimeError("Tesseract не установлен")

    monkeypatch.setattr(parsers, "ocr_pdf_page", failing_ocr)

    result = parsers._extract_pdf_with_ocr_fallback(Path("dummy.pdf"))
    assert "Е" * 300 in result
    assert "не удалось распознать" in result


def test_empty_pdf_returns_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_pages(monkeypatch, [])
    assert parsers._extract_pdf_with_ocr_fallback(Path("dummy.pdf")) == ""
