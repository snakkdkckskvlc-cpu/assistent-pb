"""Одинаковые имена файлов не перетирают друг друга.

Это не про удобство. Двое сотрудников присылают «Договор.docx»; без
уникального имени второй файл ложится поверх первого, и первый сотрудник
получает в анализ ЧУЖОЙ документ — с чужими условиями и чужими суммами. На
одном рабочем месте это было незаметно, на общем сервере это порча данных.

Вторая половина требования — пользователь не должен видеть служебный префикс:
он получает «Договор_исправленный.docx», а не «3f9a1c02_Договор_исправленный.docx».
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import UploadFile
from fire_safety_backend import config
from fire_safety_backend.infrastructure import secure_files
from fire_safety_backend.services.uploads import (
    original_name,
    text_from_input_with_source,
    unique_name,
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("UPLOAD_DIR", "WORK_DIR"):
        target = tmp_path / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, name, target)
    yield
    secure_files.reset()


def _upload(name: str, data: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(data))


# --- Уникальность ---


def test_same_name_twice_gives_different_files() -> None:
    first = unique_name("Договор.docx")
    second = unique_name("Договор.docx")
    assert first != second
    assert first.endswith("_Договор.docx")


def test_prefix_is_added_even_to_empty_name() -> None:
    assert unique_name(None).endswith("_upload")


def test_path_traversal_in_name_is_stripped() -> None:
    """Имя приходит от клиента — каталоги из него быть не должно."""
    name = unique_name("../../../etc/passwd")
    assert "/" not in name
    assert "\\" not in name
    assert name.endswith("_passwd")


async def test_two_uploads_with_one_name_do_not_overwrite() -> None:
    """Главный тест: первый сотрудник обязан получить СВОЙ документ."""
    first_text = "Договор поставки. Сумма 100 000 рублей."
    second_text = "Договор подряда. Сумма 999 999 рублей."

    text_a, _, path_a = await text_from_input_with_source(
        _upload("Договор.txt", first_text.encode()), None
    )
    text_b, _, path_b = await text_from_input_with_source(
        _upload("Договор.txt", second_text.encode()), None
    )

    assert path_a != path_b
    assert text_a == first_text
    assert text_b == second_text
    # Оба файла на диске, ни один не затёрт.
    assert len(list(config.UPLOAD_DIR.iterdir())) == 2
    assert secure_files.load(path_a).decode() == first_text


# --- Имя для пользователя ---


def test_original_name_strips_the_prefix() -> None:
    assert original_name("3f9a1c02_Договор.docx") == "Договор.docx"


def test_original_name_leaves_plain_names_alone() -> None:
    assert original_name("Договор.docx") == "Договор.docx"


def test_original_name_accepts_a_path() -> None:
    assert original_name(Path("data/uploads/3f9a1c02_Акт.pdf")) == "Акт.pdf"


def test_original_name_strips_only_one_prefix() -> None:
    """Служебный префикс ровно один — второй такой же принадлежит имени файла."""
    assert original_name("aabbccdd_11223344_отчёт.docx") == "11223344_отчёт.docx"


def test_uppercase_hex_is_not_a_prefix() -> None:
    """uuid4().hex даёт только нижний регистр — верхний принадлежит имени."""
    assert original_name("3F9A1C02_Договор.docx") == "3F9A1C02_Договор.docx"


def test_corrected_document_name_has_no_prefix(tmp_path: Path, monkeypatch) -> None:
    """Пользователь скачивает «Договор_исправленный.docx», а не файл со
    служебным префиксом в имени."""
    from docx import Document
    from fire_safety_backend.infrastructure.generators.corrected_docx import (
        build_corrected_docx,
    )

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "out")
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    source = config.UPLOAD_DIR / "3f9a1c02_Договор.docx"
    doc = Document()
    doc.add_paragraph("Текст договора.")
    doc.save(source)

    out, _ = build_corrected_docx("Текст договора.", [], source)
    assert out.name == "Договор_исправленный.docx"
