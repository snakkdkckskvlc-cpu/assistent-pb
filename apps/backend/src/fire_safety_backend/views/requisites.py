"""Роутер: проверка реквизитов во всей таблице.

Без очереди и без модели, как и проверка арифметики: контрольные суммы — это
умножение и остаток от деления, десять тысяч строк уходят за секунду. Ставить
такое в общую очередь за чужим договором, который считается восемь минут,
незачем.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import config
from ..infrastructure import secure_files
from ..services import requisites_table
from ..services.uploads import original_name, read_limited, unique_name
from . import auth

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/requisites", tags=["requisites"])

_SUFFIXES = (".xlsx", ".xlsm")


def _проверить(логический: Path) -> dict:
    # Файл на диске лежит зашифрованным, а читалка Excel умеет работать только
    # с обычным файлом — поэтому берётся расшифрованная копия.
    with secure_files.plaintext(логический) as читаемый:
        итог = requisites_table.проверить(Path(читаемый))

    return {
        "файл": original_name(логический),
        "лист": итог.лист,
        "строк": итог.строк,
        "ячеек": итог.ячеек,
        "всё_сходится": итог.всё_сходится,
        "всего_проблем": итог.всего_проблем,
        # Как программа поняла шапку. Показывается ВСЕГДА, а не только при
        # ошибке: разбор заголовков — догадка, и человек должен увидеть, что за
        # какую колонку её приняли, прежде чем поверить отчёту.
        "колонки": [
            {"буква": к.буква, "заголовок": к.заголовок, "виды": к.виды} for к in итог.колонки
        ],
        "проблемы": [
            {
                "строка": p.строка,
                "колонка": p.колонка,
                "заголовок": p.заголовок,
                "вид": p.вид,
                "значение": p.значение,
                "что": p.что,
            }
            for p in итог.проблемы
        ],
        "заметки": итог.заметки,
    }


@router.post("")
async def api_check(
    файл: UploadFile = File(...), user: auth.User = Depends(auth.current_user)
) -> dict:
    """Проходит таблицу и возвращает список испорченных реквизитов."""
    if Path(файл.filename or "").suffix.lower() not in _SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="Проверять можно файлы Excel (.xlsx). Сохраните таблицу в этом формате.",
        )
    payload = await read_limited(файл)
    логический = config.UPLOAD_DIR / unique_name(файл.filename)
    try:
        secure_files.store(логический, payload)
    except secure_files.StorageUnprotected as e:
        # Шифрование обещано, но не работает: реестр контрагентов со счетами на
        # диск открытым текстом не ложится.
        raise HTTPException(status_code=500, detail=str(e)) from e

    try:
        return await asyncio.to_thread(_проверить, логический)
    except Exception as e:  # noqa: BLE001
        log.warning("Проверка реквизитов не удалась: %s", e, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail="Не удалось прочитать файл. Проверьте, что это таблица Excel и она не повреждена.",
        ) from e
