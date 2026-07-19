"""Юнит-тесты примеров стиля из архива писем в пайплайне письма.

См. pipelines/letter.py::_style_examples_block и
packages/rag/src/fire_safety_rag/letters.py (коллекция letters_history).
"""

from __future__ import annotations

import pytest
from fire_safety_backend.pipelines import letter as letter_module
from fire_safety_backend.pipelines.letter import _EXAMPLE_MAX_CHARS, _style_examples_block


def test_examples_included_in_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        letter_module,
        "retrieve_letters",
        lambda query, top_k=2: [
            {"text": "Уважаемый Иван Иванович! Сообщаем о завершении монтажа.", "source": "a.docx"},
            {"text": "Направляем коммерческое предложение по ТО АПС.", "source": "b.docx"},
        ],
    )
    block = _style_examples_block("напомнить про ТО")
    assert "Пример 1" in block
    assert "Пример 2" in block
    assert "завершении монтажа" in block
    assert "ЗАПРЕЩЕНО переносить" in block


def test_no_examples_returns_empty_block(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(letter_module, "retrieve_letters", lambda query, top_k=2: [])
    assert _style_examples_block("любой набросок") == ""


def test_retriever_error_degrades_to_empty_block(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(query, top_k=2):
        raise RuntimeError("chroma сломалась")

    monkeypatch.setattr(letter_module, "retrieve_letters", boom)
    assert _style_examples_block("любой набросок") == ""


def test_long_example_is_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        letter_module,
        "retrieve_letters",
        lambda query, top_k=2: [{"text": "х" * (_EXAMPLE_MAX_CHARS * 3), "source": "long.docx"}],
    )
    block = _style_examples_block("набросок")
    assert len(block) < _EXAMPLE_MAX_CHARS * 2


async def test_run_letter_passes_examples_to_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        captured["user"] = user
        return {"тема": "т", "обращение": "о", "тело": "т", "формула_вежливости": "ф"}

    from fire_safety_backend.infrastructure import llm

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    monkeypatch.setattr(
        letter_module,
        "retrieve_letters",
        lambda query, top_k=2: [{"text": "Образец фирменного стиля.", "source": "s.docx"}],
    )

    def fake_build_docx(letter: dict, output_path):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake")
        return output_path

    from fire_safety_backend.infrastructure.generators import letter_docx

    monkeypatch.setattr(letter_docx, "build_letter_docx", fake_build_docx)

    import tempfile
    from pathlib import Path

    from fire_safety_backend import config

    monkeypatch.setattr(config, "OUTPUT_DIR", Path(tempfile.mkdtemp()))

    await letter_module.run_letter("напомнить о встрече", "заказчик")

    assert "Образец фирменного стиля." in captured["user"]
    assert "НАБРОСОК ПОЛЬЗОВАТЕЛЯ" in captured["user"]
    # Примеры идут ДО наброска — модель читает контекст до задачи.
    assert captured["user"].index("Образец") < captured["user"].index("НАБРОСОК")
