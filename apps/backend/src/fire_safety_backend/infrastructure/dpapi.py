"""Windows DPAPI (CryptProtectData/CryptUnprotectData) через ctypes.

Зачем именно DPAPI, а не AES из библиотеки:

Приложение шифрует документы на машине, где само и работает, — значит ключ
неизбежно лежит рядом с данными. Единственное, что реально меняет расклад, —
чтобы ключ был привязан не к файлу на диске, а к УЧЁТНОЙ ЗАПИСИ Windows.
Именно это делает DPAPI: ключ производный от учётных данных пользователя,
master-ключи лежат в %APPDATA%\\Microsoft\\Protect и нашему коду недоступны.
Скопированная на другую машину папка data/ становится бесполезной, вторая
учётная запись на этой же машине наши файлы тоже не прочитает.

Чего DPAPI НЕ даёт: защиты от кода, выполняемого от имени ЭТОГО же
пользователя, — ему Windows расшифрует всё сама. Против этого работает только
не хранить файлы (см. services/retention.py). Подробнее — docs/SECURITY.md.

Библиотека `cryptography` для AES-GCM сюда не тянется намеренно: она стала бы
новой зависимостью в requirements-runtime.txt, а установщик и автообновление в
этом проекте уже ломались. Замер показал, что скорости DPAPI хватает с
запасом: 0.5 МБ — 0.07 c, 8 МБ — 0.52 c, 64 МБ (потолок загрузки) — 4.3 c,
при том что сама модель обрабатывает документ минутами.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

log = logging.getLogger(__name__)


class DpapiUnavailable(RuntimeError):
    """DPAPI на этой системе недоступен (не Windows / crypt32 не загрузилась)."""


class DpapiError(OSError):
    """Вызов DPAPI вернул ошибку."""


# Запрещает DPAPI показывать любой UI. Обязательно: приложение работает под
# pythonw.exe без консоли, и диалог, который некому закрыть, подвесил бы
# обработку документа насмерть. С этим флагом вызов честно падает с ошибкой.
_CRYPTPROTECT_UI_FORBIDDEN = 0x1

# Типы Windows расписаны обычными ctypes вместо ctypes.wintypes намеренно:
# `import ctypes.wintypes` на Linux падает с ValueError, а этот модуль
# импортируется всегда (тесты и CI гоняются на ubuntu).
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int
_LPCWSTR = ctypes.c_wchar_p
_HLOCAL = ctypes.c_void_p
_PCHAR = ctypes.POINTER(ctypes.c_char)


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", _DWORD), ("pbData", _PCHAR)]


def _load() -> tuple[Any, Any]:
    """(crypt32, kernel32) или (None, None), если DPAPI тут быть не может."""
    if sys.platform != "win32":
        return None, None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except OSError as e:  # pragma: no cover — на Windows не воспроизводится
        log.warning("DPAPI: не удалось загрузить crypt32/kernel32: %s", e)
        return None, None

    blob_p = ctypes.POINTER(_DATA_BLOB)
    for fn in (crypt32.CryptProtectData, crypt32.CryptUnprotectData):
        fn.argtypes = [
            blob_p,  # pDataIn
            _LPCWSTR,  # szDataDescr
            blob_p,  # pOptionalEntropy
            ctypes.c_void_p,  # pvReserved
            ctypes.c_void_p,  # pPromptStruct
            _DWORD,  # dwFlags
            blob_p,  # pDataOut
        ]
        fn.restype = _BOOL
    kernel32.LocalFree.argtypes = [_HLOCAL]
    kernel32.LocalFree.restype = _HLOCAL
    return crypt32, kernel32


_crypt32, _kernel32 = _load()


def _in_blob(data: bytes) -> tuple[_DATA_BLOB, ctypes.Array]:
    """Входной DATA_BLOB и буфер, на который он ссылается, — ОДНИМ кортежем.

    Возвращать только структуру нельзя: она держит буфер сырым указателем, а
    не ссылкой, поэтому созданный внутри функции create_string_buffer может
    быть собран сборщиком мусора сразу после return — и DPAPI прочитает
    освобождённую память. Вызывающий обязан держать буфер живым до конца
    вызова, поэтому буфер и отдаётся наружу.
    """
    # max(..., 1): буфер нулевой длины создать нельзя, а cbData=0 при
    # непустом указателе DPAPI устраивает (пустой файл — законный вход).
    buf = ctypes.create_string_buffer(data, max(len(data), 1))
    blob = _DATA_BLOB(len(data), ctypes.cast(buf, _PCHAR))
    return blob, buf


def _take_out_blob(blob: _DATA_BLOB) -> bytes:
    """Копирует результат из блоба и освобождает выделенную DPAPI память."""
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        _kernel32.LocalFree(blob.pbData)


def _call(fn_name: str, data: bytes, entropy: bytes) -> bytes:
    if _crypt32 is None:
        raise DpapiUnavailable("DPAPI доступен только на Windows")
    fn = getattr(_crypt32, fn_name)

    in_blob, in_buf = _in_blob(data)
    ent_blob, ent_buf = _in_blob(entropy) if entropy else (None, None)
    out_blob = _DATA_BLOB()

    ok = fn(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob) if ent_blob is not None else None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    # Буферы должны быть живы до этой точки — см. комментарий в _in_blob.
    del in_buf, ent_buf
    if not ok:
        code = ctypes.get_last_error()
        raise DpapiError(code, f"{fn_name}: {ctypes.WinError(code).strerror}")
    return _take_out_blob(out_blob)


def protect(data: bytes, entropy: bytes = b"") -> bytes:
    """Шифрует данные ключом текущей учётной записи Windows.

    entropy — дополнительная «соль вызова»: расшифровать блоб можно только
    предъявив её же. Не секрет (лежит в нашем же коде), но отсекает случайную
    расшифровку сторонним инструментом, который просто перебирает DPAPI-блобы.
    """
    return _call("CryptProtectData", data, entropy)


def unprotect(data: bytes, entropy: bytes = b"") -> bytes:
    """Расшифровывает то, что зашифровал protect() с той же entropy.

    Целостность проверяет сам DPAPI (внутри блоба HMAC): порча шифротекста и
    несовпадение entropy дают ошибку, а не мусор на выходе.
    """
    return _call("CryptUnprotectData", data, entropy)


_self_check: bool | None = None


def is_available() -> bool:
    """Работает ли DPAPI ЗДЕСЬ И СЕЙЧАС — проверено настоящим roundtrip'ом.

    Не «мы на Windows»: master-ключи пользователя могут быть повреждены (в
    частности, после принудительного сброса пароля администратором), и тогда
    вызовы падают, хотя платформа та самая. Ответить «доступен» в таком
    состоянии — значит показать пользователю зелёный индикатор шифрования и
    уронить его первую же загрузку документа.

    Результат кешируется: проба дешёвая, но health опрашивается постоянно.
    """
    global _self_check
    if _self_check is None:
        probe = b"assistent-pb self check"
        try:
            _self_check = unprotect(protect(probe, b"probe"), b"probe") == probe
        except (DpapiUnavailable, DpapiError, OSError) as e:
            if sys.platform == "win32":
                log.error("DPAPI недоступен на Windows: %s", e)
            _self_check = False
    return _self_check
