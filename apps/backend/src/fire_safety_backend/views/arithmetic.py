"""Роутер: проверка арифметики в счёте, акте или смете.

Без очереди и без модели: это сложение столбиком, доли секунды даже на смете
в тысячу строк. Ставить такое в общую очередь за чужим договором, который
считается восемь минут, незачем.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from .. import config
from ..infrastructure import secure_files
from ..infrastructure.parsers.xlsx_parser import read_table
from ..services import arithmetic
from ..services.uploads import original_name, read_limited, unique_name
from . import auth

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/arithmetic", tags=["arithmetic"])

_SUFFIXES = (".xlsx", ".xlsm")


def _посчитать(логический: Path) -> dict:
    # Файл на диске лежит зашифрованным, а читалка Excel умеет работать только
    # с обычным файлом — поэтому берётся расшифрованная копия.
    with secure_files.plaintext(логический) as читаемый:
        разбор = read_table(Path(читаемый))
    итог = arithmetic.проверить(разбор)

    подписи = {
        "строка": "В строке",
        "итог": "Итог",
        "ндс": "НДС",
        "прописью": "Сумма прописью",
    }
    return {
        "файл": original_name(логический),
        "лист": разбор.лист,
        "строк_проверено": итог.строк_проверено,
        "сумма_строк": итог.сумма_строк,
        "итог_в_документе": итог.итог_в_документе,
        "ндс_в_документе": итог.ндс_в_документе,
        "прописью_в_документе": итог.прописью_в_документе,
        "всё_сходится": итог.всё_сходится,
        "проблемы": [
            {
                "где": подписи.get(p.где, p.где),
                "что": p.что,
                "ожидалось": p.ожидалось,
                "в_документе": p.в_документе,
                "разница": p.разница,
                "строка": p.строка,
            }
            for p in итог.проблемы
        ],
        # Обо всём, что проверить НЕ удалось, надо сказать вслух: иначе человек
        # решит, что раз расхождений нет — значит всё сошлось.
        "заметки": итог.заметки + разбор.предупреждения,
    }


@router.post("")
async def api_check(
    файл: UploadFile = File(...), user: auth.User = Depends(auth.current_user)
) -> dict:
    """Пересчитывает документ и возвращает список расхождений."""
    if Path(файл.filename or "").suffix.lower() not in _SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="Проверять можно файлы Excel (.xlsx). Сохраните документ в этом формате.",
        )
    payload = await read_limited(файл)
    логический = config.UPLOAD_DIR / unique_name(файл.filename)
    try:
        secure_files.store(логический, payload)
    except secure_files.StorageUnprotected as e:
        # Шифрование обещано, но не работает: счёт с ценами заказчика на диск
        # открытым текстом не ложится.
        raise HTTPException(status_code=500, detail=str(e)) from e

    try:
        return await asyncio.to_thread(_посчитать, логический)
    except Exception as e:  # noqa: BLE001
        log.warning("Проверка арифметики не удалась: %s", e, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail="Не удалось прочитать файл. Проверьте, что это таблица Excel и она не повреждена.",
        ) from e
