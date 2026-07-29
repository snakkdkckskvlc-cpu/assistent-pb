"""EasyOCR как необязательная замена Tesseract.

Сам easyocr в проект не ставится (тянет torch), поэтому здесь подменяется
модуль-заглушка с той же формой ответа: readtext(detail=1) возвращает список
кортежей (bbox, текст, уверенность 0..1).

Ключевое поведение: фрагмент с низкой уверенностью заменяется на `[?]`.
Для юр. анализа явная дыра честнее правдоподобной подделки — на реальном
скане Tesseract при средней уверенности ~80 выдавал «г. Линецк» вместо
«г. Липецк», и модель цитировала это как текст договора.
"""

from __future__ import annotations

import sys
import types

import pytest
from fire_safety_backend import config
from fire_safety_backend.infrastructure.parsers import ocr


class _FakeReader:
    def __init__(self, result: list[tuple]) -> None:
        self._result = result
        self.calls = 0

    def readtext(self, image, detail: int = 1, paragraph: bool = False) -> list[tuple]:
        self.calls += 1
        return self._result


@pytest.fixture(autouse=True)
def _reset_easyocr_cache() -> None:
    """Модуль кэширует Reader в глобальной переменной — между тестами сбрасываем."""
    ocr._easyocr_reader = None
    ocr._easyocr_unavailable = False
    yield
    ocr._easyocr_reader = None
    ocr._easyocr_unavailable = False


def _install_fake_easyocr(monkeypatch: pytest.MonkeyPatch, reader: _FakeReader) -> None:
    module = types.ModuleType("easyocr")
    module.Reader = lambda langs, gpu=False: reader
    monkeypatch.setitem(sys.modules, "easyocr", module)


def test_low_confidence_fragment_is_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _FakeReader(
        [
            ([[0, 0], [1, 0], [1, 1], [0, 1]], "ДОГОВОР ПОДРЯДА", 0.95),
            ([[0, 2], [1, 2], [1, 3], [0, 3]], "г. Линецк", 0.41),
            ([[0, 4], [1, 4], [1, 5], [0, 5]], "01 января 2026", 0.88),
        ]
    )
    _install_fake_easyocr(monkeypatch, reader)

    text, confidence = ocr._easyocr_read(object())

    assert "ДОГОВОР ПОДРЯДА" in text
    assert "01 января 2026" in text
    assert "г. Линецк" not in text, "фрагмент ниже порога обязан быть скрыт"
    assert "[?]" in text
    # Уверенность приводится к общей с Tesseract шкале 0..100.
    assert confidence == pytest.approx(74.7, abs=0.1)


def test_confidence_threshold_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _FakeReader([([], "спорный текст", 0.5)])
    _install_fake_easyocr(monkeypatch, reader)

    monkeypatch.setattr(config, "EASYOCR_MIN_CONFIDENCE", 0.4)
    assert "спорный текст" in ocr._easyocr_read(object())[0]

    ocr._easyocr_reader = None
    _install_fake_easyocr(monkeypatch, reader)
    monkeypatch.setattr(config, "EASYOCR_MIN_CONFIDENCE", 0.9)
    assert ocr._easyocr_read(object())[0] == "[?]"


def test_empty_result_returns_no_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_easyocr(monkeypatch, _FakeReader([]))
    assert ocr._easyocr_read(object()) == ("", None)


def test_reader_is_created_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reader поднимает модели в память — создавать его на каждую страницу
    значит терять секунды впустую."""
    created: list[int] = []
    reader = _FakeReader([([], "текст", 0.9)])
    module = types.ModuleType("easyocr")

    def _make(langs, gpu=False):
        created.append(1)
        return reader

    module.Reader = _make
    monkeypatch.setitem(sys.modules, "easyocr", module)

    ocr._load_easyocr()
    ocr._load_easyocr()
    ocr._load_easyocr()
    assert len(created) == 1


def test_missing_easyocr_falls_back_silently() -> None:
    """Пакета нет — всё продолжает работать на Tesseract, без исключения.

    easyocr в проект не ставится, поэтому обычный импорт здесь и правда
    падает: подменять ничего не нужно, это ровно боевая ситуация.
    """
    assert "easyocr" not in sys.modules
    assert ocr._load_easyocr() is None
    # Повторная попытка не должна снова лезть в импорт — флаг запомнен.
    assert ocr._easyocr_unavailable is True


def test_disabled_by_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "USE_EASYOCR", False)
    assert ocr._load_easyocr() is None


def test_broken_reader_does_not_break_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Модели easyocr могут не скачаться офлайн — это не повод падать."""
    module = types.ModuleType("easyocr")

    def _explode(langs, gpu=False):
        raise RuntimeError("модели не найдены")

    module.Reader = _explode
    monkeypatch.setitem(sys.modules, "easyocr", module)
    assert ocr._load_easyocr() is None


def test_read_image_uses_tesseract_when_easyocr_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "USE_EASYOCR", False)
    monkeypatch.setattr(ocr, "ocr_confidence", lambda image: 88.0)

    fake_pytesseract = types.SimpleNamespace(
        image_to_string=lambda image, lang=None: "из тессеракта"
    )
    monkeypatch.setattr(ocr, "_load_tesseract", lambda: (fake_pytesseract, None))

    assert ocr._read_image(object()) == ("из тессеракта", 88.0)
