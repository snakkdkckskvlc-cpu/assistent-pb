"""Автоочистка рабочих файлов.

Зачем она нужна: шифрование ключом учётной записи Windows не защищает от кода,
запущенного под этой же учётной записью. От накопленных за годы договоров
спасает только их отсутствие.

Отсюда и требования к тестам: удалять надо ровно старое (не свежее — иначе
очистка съест документ, который прямо сейчас обрабатывается), сбой на одном
файле не должен обрывать проход, а `0` дней обязан выключать всё целиком.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fire_safety_backend import config
from fire_safety_backend.services import retention

_DAY = 24 * 60 * 60

# Считывается на импорте — до того, как фикстура подменит значение на 7.
_CONFIGURED_RETENTION_DAYS = config.DATA_RETENTION_DAYS


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "tmp")
    for d in (config.UPLOAD_DIR, config.OUTPUT_DIR, config.WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "DATA_RETENTION_DAYS", 7)


def _file(directory: Path, name: str, age_days: float, size: int = 100) -> Path:
    path = directory / name
    path.write_bytes(b"x" * size)
    when = time.time() - age_days * _DAY
    os.utime(path, (when, when))
    return path


def _dir(parent: Path, name: str, age_days: float) -> Path:
    path = parent / name
    path.mkdir()
    (path / "документ.docx").write_bytes(b"x" * 50)
    when = time.time() - age_days * _DAY
    os.utime(path, (when, when))
    return path


def test_expired_files_removed_fresh_kept() -> None:
    old = _file(config.UPLOAD_DIR, "старый.docx", age_days=10)
    fresh = _file(config.UPLOAD_DIR, "свежий.docx", age_days=1)

    stats = retention.purge_expired()

    assert not old.exists()
    assert fresh.exists(), "свежий документ могли обрабатывать прямо сейчас"
    assert stats["uploads"] == 1


def test_boundary_file_at_the_limit_is_kept() -> None:
    """Ровно на границе — оставляем: удалять «уже можно» мы не обязаны."""
    edge = _file(config.OUTPUT_DIR, "граница.docx", age_days=6.9)
    retention.purge_expired()
    assert edge.exists()


def test_counts_and_freed_bytes() -> None:
    _file(config.UPLOAD_DIR, "a.docx", age_days=10, size=1000)
    _file(config.UPLOAD_DIR, "b.docx", age_days=10, size=2000)
    _file(config.OUTPUT_DIR, "c.docx", age_days=10, size=3000)

    stats = retention.purge_expired()

    assert stats["uploads"] == 2
    assert stats["outputs"] == 1
    assert stats["freed_bytes"] == 6000
    assert stats["disabled"] is False


def test_zero_days_disables_cleanup() -> None:
    """Оператор может решить хранить бессрочно — тогда мы не трогаем ничего."""
    ancient = _file(config.UPLOAD_DIR, "древний.docx", age_days=1000)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "DATA_RETENTION_DAYS", 0)
        stats = retention.purge_expired()
    assert ancient.exists()
    assert stats["disabled"] is True
    assert stats["uploads"] == 0


def test_one_stuck_file_does_not_abort_the_sweep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Файл может быть открыт в Word. Это не повод бросать остальные."""
    stuck = _file(config.UPLOAD_DIR, "занят.docx", age_days=10)
    other = _file(config.UPLOAD_DIR, "тоже_старый.docx", age_days=10)

    real_remove = retention._remove

    def flaky_remove(entry: Path) -> int:
        if entry.name == "занят.docx":
            raise OSError(32, "файл используется другим процессом")
        return real_remove(entry)

    monkeypatch.setattr(retention, "_remove", flaky_remove)
    stats = retention.purge_expired()

    assert stuck.exists()
    assert not other.exists()
    assert stats["uploads"] == 1


def test_missing_directory_is_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "UPLOAD_DIR", config.UPLOAD_DIR / "нет-такой")
    assert retention.purge_expired()["uploads"] == 0


# --- data/tmp: расшифрованные копии ---


def test_stale_work_dirs_removed() -> None:
    """Брошенный каталог с расшифрованной копией — открытый документ на диске."""
    stale = _dir(config.WORK_DIR, "doc_старый", age_days=10)
    retention.purge_expired()
    assert not stale.exists()


def test_fresh_work_dir_survives_even_full_purge() -> None:
    """С этой копией МОЖЕТ работать задача прямо сейчас: OCR большого скана и
    юр. анализ идут минутами. Удалить её — уронить обработку на середине."""
    fresh = _dir(config.WORK_DIR, "doc_свежий", age_days=0)
    stats = retention.purge_all()
    assert fresh.exists()
    assert stats["tmp"] == 0


# --- Ручная очистка ---


def test_purge_all_removes_regardless_of_age() -> None:
    fresh_upload = _file(config.UPLOAD_DIR, "сегодняшний.docx", age_days=0)
    fresh_output = _file(config.OUTPUT_DIR, "письмо.docx", age_days=0)

    stats = retention.purge_all()

    assert not fresh_upload.exists()
    assert not fresh_output.exists()
    assert stats["uploads"] == 1
    assert stats["outputs"] == 1


def test_purge_all_ignores_retention_setting() -> None:
    """Кнопка «удалить» обязана работать и при выключенной автоочистке."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "DATA_RETENTION_DAYS", 0)
        target = _file(config.UPLOAD_DIR, "документ.docx", age_days=0)
        retention.purge_all()
    assert not target.exists()


def test_default_retention_is_sane() -> None:
    """Слишком долго — документы копятся, слишком коротко — исчезают из-под рук."""
    assert 1 <= _CONFIGURED_RETENTION_DAYS <= 90
