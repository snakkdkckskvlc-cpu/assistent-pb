"""Слой прозрачного шифрования рабочих файлов.

Главный тест здесь — `test_plaintext_is_not_on_disk`: он единственный
доказывает, что шифрование вообще происходит. Остальное — свойства, без
которых слой ломает приложение: старые открытые файлы должны читаться,
расшифрованная копия обязана исчезать (в том числе при исключении), а отказ
шифрования не должен превращаться в тихую запись договора открытым текстом.

Протектор подставляется: DPAPI существует только на Windows, а проверять
логику конверта надо и в CI на ubuntu. Сам DPAPI покрыт test_dpapi.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fire_safety_backend import config
from fire_safety_backend.infrastructure import secure_files

_SECRET = "Договор поставки. Цена 1 500 000 рублей.".encode()


class _XorProtector:
    """Не криптография, а маркер: содержимое ФАЙЛА отличается от исходного.

    Настоящий шифр здесь не нужен — проверяется поведение слоя, а стойкость
    DPAPI проверяется там, где он есть.
    """

    name = "xor"
    _KEY = 0x5A

    def protect(self, data: bytes) -> bytes:
        return bytes(b ^ self._KEY for b in data)

    def unprotect(self, data: bytes) -> bytes:
        return bytes(b ^ self._KEY for b in data)


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "WORK_DIR", tmp_path / "work")
    for d in (config.UPLOAD_DIR, config.OUTPUT_DIR, config.WORK_DIR):
        d.mkdir(parents=True, exist_ok=True)
    secure_files.use_protector(_XorProtector())
    yield
    secure_files.reset()


@pytest.fixture
def logical(tmp_path: Path) -> Path:
    return config.UPLOAD_DIR / "договор.docx"


# --- Шифрование ---


def test_plaintext_is_not_on_disk(logical: Path) -> None:
    """Ради этого всё и делается: содержимого документа на диске нет."""
    stored = secure_files.store(logical, _SECRET)
    assert stored.name == "договор.docx.enc"
    assert not logical.exists()
    raw = stored.read_bytes()
    assert _SECRET not in raw
    assert b"1 500 000" not in raw


def test_envelope_has_magic(logical: Path) -> None:
    """Магия отличает наш конверт от файла, оставшегося открытым."""
    stored = secure_files.store(logical, _SECRET)
    assert stored.read_bytes().startswith(secure_files.MAGIC)


def test_roundtrip(logical: Path) -> None:
    secure_files.store(logical, _SECRET)
    assert secure_files.load(logical) == _SECRET


def test_empty_file_roundtrip(logical: Path) -> None:
    """Пустой файл — законный вход (пользователь может загрузить и такой)."""
    secure_files.store(logical, b"")
    assert secure_files.load(logical) == b""


def test_store_removes_stale_plaintext(logical: Path) -> None:
    """Иначе документ зашифрован, а рядом лежит его читаемая копия."""
    logical.write_bytes(_SECRET)
    secure_files.store(logical, _SECRET)
    assert not logical.exists()


# --- Совместимость со старыми файлами ---


def test_legacy_plaintext_still_readable(logical: Path) -> None:
    """Файлы с прошлых версий не должны стать «повреждёнными»."""
    logical.write_bytes(_SECRET)
    assert secure_files.load(logical) == _SECRET
    assert secure_files.stored_path(logical) == logical


def test_encrypted_wins_over_plaintext(logical: Path) -> None:
    logical.write_bytes("устаревшая версия".encode())
    secure_files.encrypted_path(logical).write_bytes(
        secure_files.MAGIC + _XorProtector().protect(_SECRET)
    )
    assert secure_files.load(logical) == _SECRET


def test_missing_file_raises(logical: Path) -> None:
    with pytest.raises(FileNotFoundError):
        secure_files.load(logical)


# --- Расшифрованная копия ---


def test_plaintext_context_gives_real_file_with_original_name(logical: Path) -> None:
    """Имя обязано сохраниться: по расширению выбирается парсер, а по stem —
    имя исправленного документа."""
    secure_files.store(logical, _SECRET)
    with secure_files.plaintext(logical) as readable:
        assert readable.name == "договор.docx"
        assert readable.read_bytes() == _SECRET
        temp_dir = readable.parent
        # Именно в data/tmp, а не в системном %TEMP%: иначе открытая копия
        # остаётся там, куда не дотягивается автоочистка.
        assert temp_dir.parent == config.WORK_DIR
    assert not readable.exists()
    assert not temp_dir.exists()


def test_plaintext_copy_removed_after_exception(logical: Path) -> None:
    """Копия не должна переживать сбой обработки — иначе открытый договор
    остаётся на диске именно в тех случаях, когда никто не смотрит."""
    secure_files.store(logical, _SECRET)
    leaked: Path | None = None
    with pytest.raises(RuntimeError), secure_files.plaintext(logical) as readable:
        leaked = readable
        raise RuntimeError("обработка упала")
    assert leaked is not None
    assert not leaked.exists()
    assert list(config.WORK_DIR.iterdir()) == []


def test_plaintext_of_legacy_file_is_not_copied(logical: Path) -> None:
    """Открытый файл копировать некуда и незачем — иначе размножаем открытые
    документы по диску."""
    logical.write_bytes(_SECRET)
    with secure_files.plaintext(logical) as readable:
        assert readable == logical
    assert logical.exists()


# --- Запись результата генератором ---


def test_encrypted_output_encrypts_what_generator_wrote() -> None:
    logical = config.OUTPUT_DIR / "письмо.docx"
    with secure_files.encrypted_output(logical) as writable:
        assert writable.name == "письмо.docx"
        writable.write_bytes(_SECRET)
    assert not logical.exists()
    assert secure_files.load(logical) == _SECRET
    assert _SECRET not in secure_files.encrypted_path(logical).read_bytes()


def test_encrypted_output_saves_nothing_on_exception() -> None:
    """Недописанный DOCX в outputs выглядел бы как готовый файл."""
    logical = config.OUTPUT_DIR / "письмо.docx"
    with pytest.raises(RuntimeError), secure_files.encrypted_output(logical) as writable:
        writable.write_bytes("половина файла".encode())
        raise RuntimeError("генератор упал")
    assert not secure_files.exists(logical)
    assert list(config.WORK_DIR.iterdir()) == []


# --- Отказы ---


def test_broken_encryption_refuses_to_store(logical: Path) -> None:
    """Шифрование обещано, но не работает: лучше понятная ошибка, чем
    договор, тихо положенный на диск открытым текстом."""
    secure_files.use_protector(None, broken=True)
    with pytest.raises(secure_files.StorageUnprotected):
        secure_files.store(logical, _SECRET)
    assert not logical.exists()
    assert not secure_files.encrypted_path(logical).exists()


def test_broken_encryption_refuses_generator_output() -> None:
    secure_files.use_protector(None, broken=True)
    with (
        pytest.raises(secure_files.StorageUnprotected),
        secure_files.encrypted_output(config.OUTPUT_DIR / "письмо.docx"),
    ):
        pytest.fail("до тела блока доходить не должно")


def test_disabled_encryption_writes_plain_file(logical: Path) -> None:
    """Выключено осознанно (ENCRYPT_AT_REST=0) — пишем как раньше."""
    secure_files.use_protector(None)
    stored = secure_files.store(logical, _SECRET)
    assert stored == logical
    assert logical.read_bytes() == _SECRET
    assert not secure_files.encrypted_path(logical).exists()


def test_encrypted_file_without_protector_gives_clear_error(logical: Path) -> None:
    """Файл зашифрован, а расшифровать нечем: пользователю нужна мысль
    «не та учётная запись», а не трассировка."""
    secure_files.store(logical, _SECRET)
    secure_files.use_protector(None)
    with pytest.raises(secure_files.DecryptError):
        secure_files.load(logical)


def test_corrupted_envelope_gives_clear_error(logical: Path) -> None:
    class _Failing(_XorProtector):
        def unprotect(self, data: bytes) -> bytes:
            raise OSError("порча обнаружена")

    secure_files.store(logical, _SECRET)
    secure_files.use_protector(_Failing())
    with pytest.raises(secure_files.DecryptError) as exc:
        secure_files.load(logical)
    assert "учётной записи" in str(exc.value)


# --- Состояние ---


def test_status_off_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ENCRYPT_AT_REST", False)
    secure_files.reset()
    st = secure_files.status()
    assert st.mode == "off"
    assert st.broken is False


def test_status_broken_on_windows_without_dpapi(monkeypatch: pytest.MonkeyPatch) -> None:
    """На Windows недоступный DPAPI — авария, а не «просто выключено»."""
    monkeypatch.setattr(config, "ENCRYPT_AT_REST", True)
    monkeypatch.setattr(secure_files.sys, "platform", "win32")
    monkeypatch.setattr(secure_files.dpapi, "is_available", lambda: False)
    secure_files.reset()
    st = secure_files.status()
    assert st.mode == "off"
    assert st.broken is True


def test_status_not_broken_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """На Linux DPAPI не существует — это разработка/CI, а не авария."""
    monkeypatch.setattr(config, "ENCRYPT_AT_REST", True)
    monkeypatch.setattr(secure_files.sys, "platform", "linux")
    monkeypatch.setattr(secure_files.dpapi, "is_available", lambda: False)
    secure_files.reset()
    st = secure_files.status()
    assert st.mode == "off"
    assert st.broken is False


def test_status_dpapi_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "ENCRYPT_AT_REST", True)
    monkeypatch.setattr(secure_files.dpapi, "is_available", lambda: True)
    secure_files.reset()
    assert secure_files.status().mode == "dpapi"
