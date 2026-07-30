"""Границы записи: приложение изменяет файлы только в своей рабочей папке.

Требование: приложение не должно уметь испортить или удалить документ, который
ему не передавали через интерфейс. Сейчас это соблюдается и так, поэтому тесты
здесь ЗАКРЕПЛЯЮТ инвариант: правка, которая начнёт писать наружу, упадёт тут, а
не испортит документы у заказчика.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fire_safety_backend import config
from fire_safety_backend.infrastructure import file_access, secure_files
from fire_safety_backend.services import retention


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name in ("UPLOAD_DIR", "OUTPUT_DIR", "WORK_DIR"):
        target = tmp_path / name.lower()
        target.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(config, name, target)
    yield
    secure_files.reset()


# --- Что разрешено ---


def test_upload_dir_is_writable() -> None:
    assert file_access.is_writable(config.UPLOAD_DIR / "договор.docx")


def test_output_dir_is_writable() -> None:
    assert file_access.is_writable(config.OUTPUT_DIR / "письмо.docx")


def test_work_dir_is_writable() -> None:
    assert file_access.is_writable(config.WORK_DIR / "doc_123" / "договор.docx")


def test_roots_follow_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Список читается из config при каждом вызове: закешированный сделал бы
    проверку либо бесполезной, либо ложно срабатывающей в тестах."""
    other = tmp_path / "другая"
    other.mkdir()
    monkeypatch.setattr(config, "UPLOAD_DIR", other)
    assert file_access.is_writable(other / "x.docx")


# --- Что запрещено ---


def test_arbitrary_path_is_denied(tmp_path: Path) -> None:
    with pytest.raises(file_access.AccessDenied):
        file_access.assert_writable(tmp_path / "чужой_договор.docx")


def test_parent_traversal_is_denied() -> None:
    """resolve() обязателен: без него строка начинается с разрешённого
    каталога, а файл оказывается где угодно."""
    with pytest.raises(file_access.AccessDenied):
        file_access.assert_writable(config.UPLOAD_DIR / ".." / ".." / "секрет.docx")


def test_data_dir_itself_is_denied() -> None:
    """Разрешены именно три рабочих каталога, а не data/ целиком: там же
    app.db и chroma, документному слою туда писать нечего."""
    with pytest.raises(file_access.AccessDenied):
        file_access.assert_writable(config.UPLOAD_DIR.parent / "app.db")


def test_sibling_prefix_is_not_enough(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`uploads_чужие` начинается на `uploads`, но это другой каталог.
    Сравнение строк тут дало бы ложное разрешение."""
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    with pytest.raises(file_access.AccessDenied):
        file_access.assert_writable(tmp_path / "uploads_чужие" / "x.docx")


# --- Через реальные точки записи ---


def test_store_refuses_path_outside_workdirs(tmp_path: Path) -> None:
    with pytest.raises(file_access.AccessDenied):
        secure_files.store(tmp_path / "чужой.docx", b"data")


def test_store_does_not_create_directories_outside(tmp_path: Path) -> None:
    """Проверка обязана быть ДО mkdir, иначе отклонённая запись успевает
    создать каталог в чужом месте."""
    target = tmp_path / "чужая_папка" / "документ.docx"
    with pytest.raises(file_access.AccessDenied):
        secure_files.store(target, b"data")
    assert not target.parent.exists()


def test_encrypted_output_refuses_path_outside_workdirs(tmp_path: Path) -> None:
    with (
        pytest.raises(file_access.AccessDenied),
        secure_files.encrypted_output(tmp_path / "чужой.docx"),
    ):
        pytest.fail("до тела блока доходить не должно")


def test_retention_cannot_delete_outside_workdirs(tmp_path: Path) -> None:
    """Единственная точка, где приложение удаляет файлы. Ошибка в подсчёте
    путей выше не должна превращаться в удаление чужих документов."""
    victim = tmp_path / "важный_договор.docx"
    victim.write_bytes("не удалять".encode())
    with pytest.raises(file_access.AccessDenied):
        retention._remove(victim)
    assert victim.exists()
