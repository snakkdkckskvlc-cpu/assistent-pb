"""Автоочистка рабочих файлов: data/uploads, data/outputs, data/tmp.

Зачем это вообще нужно, если файлы уже шифруются: шифрование ключом учётной
записи Windows (infrastructure/dpapi.py) не защищает от кода, запущенного под
ЭТОЙ ЖЕ учётной записью — Windows расшифрует ему всё сама. От этого спасает
только отсутствие файла. Поэтому загруженные договоры и сгенерированные письма
живут ограниченный срок (DATA_RETENTION_DAYS, по умолчанию 7 дней), а не
накапливаются годами.

Скачанные пользователем документы лежат там, куда он их сохранил, и очисткой
не затрагиваются — удаляются только рабочие копии внутри data/.

Перезаписи содержимого перед удалением здесь НЕТ намеренно. На SSD с
wear-leveling и на журналируемой NTFS перезапись «того же» файла не даёт
гарантии, что старые блоки недостижимы, — это создавало бы ощущение защиты,
которой нет. Настоящий ответ на «данные должны быть нестираемо удалены» —
шифрование диска целиком (BitLocker), см. docs/SECURITY.md.
"""

from __future__ import annotations

import logging
import shutil
import time
from typing import TYPE_CHECKING

from .. import config
from ..infrastructure import file_access

if TYPE_CHECKING:
    from pathlib import Path

log = logging.getLogger(__name__)

_SEC_PER_DAY = 24 * 60 * 60

# Минимальный возраст записи в data/tmp, ниже которого её не трогает даже
# «удалить всё». Там лежат расшифрованные копии документов, с которыми ПРЯМО
# СЕЙЧАС может работать задача (OCR большого скана и юр. анализ занимают
# минуты). Удалить такую копию — уронить обработку на середине. Часа хватает
# с запасом: дольше живут только зависшие каталоги, которые и надо убрать.
_WORK_MIN_AGE_SEC = 60 * 60


def _entry_size(entry: Path) -> int:
    if entry.is_dir():
        return sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
    return entry.stat().st_size


def _remove(entry: Path) -> int:
    """Удаляет файл или каталог, возвращает освобождённый объём.

    Проверка границ здесь, а не только в вызывающем коде: это единственная
    точка, где приложение вообще что-то удаляет, и ошибка в подсчёте путей
    выше не должна превращаться в удаление чужих файлов.
    """
    file_access.assert_writable(entry)
    size = _entry_size(entry)
    if entry.is_dir():
        shutil.rmtree(entry)
    else:
        entry.unlink()
    return size


def _sweep(
    directory: Path,
    cutoff: float | None,
    *,
    min_age_sec: float = 0.0,
) -> tuple[int, int]:
    """Чистит каталог. Возвращает (сколько удалено, сколько байт освобождено).

    cutoff — метка времени, старше которой запись удаляется; None означает
    «удалять независимо от возраста». min_age_sec — нижняя граница, которую
    не переступает даже None-режим.
    """
    if not directory.exists():
        return 0, 0

    now = time.time()
    removed = 0
    freed = 0
    for entry in sorted(directory.iterdir()):
        try:
            mtime = entry.stat().st_mtime
        except OSError as e:
            log.warning("Очистка: не удалось получить дату %s: %s", entry.name, e)
            continue
        if min_age_sec and now - mtime < min_age_sec:
            continue
        if cutoff is not None and mtime >= cutoff:
            continue
        try:
            freed += _remove(entry)
            removed += 1
        except OSError as e:
            # Файл может быть открыт в Word или занят самой обработкой.
            # Один такой файл не должен обрывать проход по остальным.
            log.warning("Очистка: не удалось удалить %s: %s", entry.name, e)
    return removed, freed


def _run(cutoff: float | None) -> dict[str, int]:
    stats: dict[str, int] = {"uploads": 0, "outputs": 0, "tmp": 0, "freed_bytes": 0}
    targets = (
        ("uploads", config.UPLOAD_DIR, 0.0),
        ("outputs", config.OUTPUT_DIR, 0.0),
        ("tmp", config.WORK_DIR, _WORK_MIN_AGE_SEC),
    )
    for key, directory, min_age in targets:
        removed, freed = _sweep(directory, cutoff, min_age_sec=min_age)
        stats[key] = removed
        stats["freed_bytes"] += freed
    return stats


def purge_expired() -> dict:
    """Удаляет всё, что прожило дольше DATA_RETENTION_DAYS."""
    days = config.DATA_RETENTION_DAYS
    if days <= 0:
        # Осознанное решение оператора: срок не задан — не удаляем ничего.
        return {"uploads": 0, "outputs": 0, "tmp": 0, "freed_bytes": 0, "disabled": True}

    stats: dict = dict(_run(time.time() - days * _SEC_PER_DAY))
    stats["disabled"] = False
    total = stats["uploads"] + stats["outputs"] + stats["tmp"]
    if total:
        log.info(
            "Автоочистка (срок %d дн.): удалено %d, освобождено %.1f МБ",
            days,
            total,
            stats["freed_bytes"] / 1024 / 1024,
        )
    return stats


def purge_all() -> dict:
    """Удаляет рабочие файлы независимо от возраста — по кнопке в интерфейсе.

    Оговорка про data/tmp: свежие расшифрованные копии не трогаются (см.
    _WORK_MIN_AGE_SEC), иначе кнопка ломала бы задачу, которая в этот момент
    обрабатывает документ.
    """
    stats = _run(None)
    log.info(
        "Ручная очистка: uploads %d, outputs %d, tmp %d, освобождено %.1f МБ",
        stats["uploads"],
        stats["outputs"],
        stats["tmp"],
        stats["freed_bytes"] / 1024 / 1024,
    )
    return stats
