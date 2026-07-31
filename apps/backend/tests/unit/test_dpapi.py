"""Обёртка над Windows DPAPI.

Проверяется не «функция что-то вернула», а свойства, на которые опирается
шифрование файлов: данные восстанавливаются один-в-один, чужая entropy не
подходит, порча шифротекста обнаруживается. Если хотя бы последнее перестанет
работать, подмена документа на диске пройдёт незамеченной.

Тесты Windows-only: DPAPI на других платформах не существует, а CI гоняется
на ubuntu.
"""

from __future__ import annotations

import sys

import pytest
from fire_safety_backend.infrastructure import dpapi

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="DPAPI есть только на Windows")

_ENTROPY = b"assistent-pb:test"


def test_available_on_windows() -> None:
    """Не «мы на Windows», а настоящая проба: is_available() делает roundtrip."""
    assert dpapi.is_available() is True


@pytest.mark.parametrize("size", [0, 1, 1024, 1024 * 1024])
def test_roundtrip(size: int) -> None:
    payload = bytes(range(256)) * (size // 256) + b"x" * (size % 256)
    assert len(payload) == size
    assert dpapi.unprotect(dpapi.protect(payload, _ENTROPY), _ENTROPY) == payload


def test_ciphertext_does_not_contain_plaintext() -> None:
    secret = "договор поставки НЛМК".encode()
    blob = dpapi.protect(secret, _ENTROPY)
    assert secret not in blob


def test_wrong_entropy_rejected() -> None:
    blob = dpapi.protect("секрет".encode(), _ENTROPY)
    with pytest.raises(OSError):
        dpapi.unprotect(blob, "другая".encode())


def test_missing_entropy_rejected() -> None:
    blob = dpapi.protect("секрет".encode(), _ENTROPY)
    with pytest.raises(OSError):
        dpapi.unprotect(blob, b"")


def test_tampered_ciphertext_rejected() -> None:
    """Иначе подмена содержимого файла на диске прошла бы незамеченной."""
    blob = bytearray(dpapi.protect("секрет".encode() * 10, _ENTROPY))
    blob[len(blob) // 2] ^= 0xFF
    with pytest.raises(OSError):
        dpapi.unprotect(bytes(blob), _ENTROPY)


def test_garbage_is_not_a_blob() -> None:
    with pytest.raises(OSError):
        dpapi.unprotect("это вообще не DPAPI-блоб".encode(), _ENTROPY)
