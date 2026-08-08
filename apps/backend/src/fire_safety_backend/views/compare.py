"""Роутер: сверка двух таблиц.

Сверка идёт синхронно, а не через очередь задач, в отличие от разбора договора.
Причина простая: здесь не участвует модель. Сравнение двух смет на тысячу строк
— это доли секунды арифметики, и заставлять человека ждать в общей очереди за
чужим договором, который считается восемь минут, незачем.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from .. import config
from ..infrastructure import secure_files
from ..infrastructure.generators.compare_xlsx import build_compare_xlsx
from ..infrastructure.parsers.xlsx_parser import read_table
from ..services import compare_memory, ownership
from ..services.table_compare import compare, normalize
from ..services.uploads import original_name, read_limited, unique_name
from . import auth


class Соответствие(BaseModel):
    """Две записи, которые человек объявил одной позицией.

    Приходят человеческие написания, как они были на экране. В ключи их
    переводит роутер: сервис памяти работает с нормализованными ключами и
    сопоставлением не занимается.
    """

    слева: str
    справа: str


class Отзыв(BaseModel):
    """Номер пары, которую человек отзывает."""

    id: int


log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/compare", tags=["compare"])

_SUFFIXES = (".xlsx", ".xlsm")


async def _save(file: UploadFile) -> Path:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="Сверять можно файлы Excel (.xlsx). Сохраните таблицу в этом формате.",
        )
    payload = await read_limited(file)
    logical = config.UPLOAD_DIR / unique_name(file.filename)
    try:
        secure_files.store(logical, payload)
    except secure_files.StorageUnprotected as e:
        # Шифрование обещано, но не работает: смета с ценами заказчика на диск
        # открытым текстом не ложится. Та же реакция, что в uploads.py.
        raise HTTPException(status_code=500, detail=str(e)) from e
    return logical


def _разобрать_и_сверить(
    слева: Path, справа: Path, лист_слева: str | None, лист_справа: str | None
) -> dict:
    # Читаем через расшифрованную копию: на диске файл лежит зашифрованным,
    # а openpyxl умеет работать только с файлом.
    with secure_files.plaintext(слева) as л_файл, secure_files.plaintext(справа) as п_файл:
        л = read_table(Path(л_файл), sheet=лист_слева)
        п = read_table(Path(п_файл), sheet=лист_справа)

    # Подтверждённые человеком соответствия — до сравнения: то, что он один раз
    # разобрал, не должно попасть в кандидаты повторно.
    отчёт = compare(л.строки, п.строки, синонимы=compare_memory.synonyms())
    имя_л, имя_п = original_name(слева), original_name(справа)
    dest = config.OUTPUT_DIR / f"Сверка {Path(имя_л).stem} и {Path(имя_п).stem}.xlsx"
    with secure_files.encrypted_output(dest) as writable:
        build_compare_xlsx(
            отчёт,
            Path(writable),
            имя_слева=имя_л,
            имя_справа=имя_п,
            предупреждения=л.предупреждения + п.предупреждения,
        )

    return {
        "файл": dest.name,
        "слева": {
            "имя": имя_л,
            "лист": л.лист,
            "строк": len(л.строки),
            "пропущено_итогов": л.пропущено_итогов,
            "колонки": л.колонки,
            "предупреждения": л.предупреждения,
        },
        "справа": {
            "имя": имя_п,
            "лист": п.лист,
            "строк": len(п.строки),
            "пропущено_итогов": п.пропущено_итогов,
            "колонки": п.колонки,
            "предупреждения": п.предупреждения,
        },
        "итог": {
            "сошлось": len(отчёт.сошлось),
            "расхождений": len(отчёт.расхождения),
            "только_слева": len(отчёт.только_слева),
            "только_справа": len(отчёт.только_справа),
            "похожих": len(отчёт.кандидаты),
            "повторов": len(отчёт.дубли_слева) + len(отчёт.дубли_справа),
            "сумма_слева": отчёт.итог_слева,
            "сумма_справа": отчёт.итог_справа,
            "разница": отчёт.расхождение_итогов,
            "всё_сошлось": отчёт.всё_сошлось,
        },
        # Первые строки каждой категории — чтобы человек увидел результат на
        # экране и решил, надо ли скачивать файл.
        "расхождения": [
            {
                "название": п_.слева.название,
                "строка": п_.слева.номер,
                "кол_слева": п_.слева.количество,
                "кол_справа": п_.справа.количество,
                "разница_кол": п_.разница_количества,
                "сум_слева": п_.слева.сумма,
                "сум_справа": п_.справа.сумма,
                "разница_сум": п_.разница_суммы,
            }
            for п_ in отчёт.расхождения[:200]
        ],
        "нет_справа": [
            {"название": r.название, "строка": r.номер, "сумма": r.сумма}
            for r in отчёт.только_слева[:200]
        ],
        "нет_слева": [
            {"название": r.название, "строка": r.номер, "сумма": r.сумма}
            for r in отчёт.только_справа[:200]
        ],
        "похожие": [
            {
                "слева": k.слева.название,
                "строка_слева": k.слева.номер,
                "справа": k.справа.название,
                "строка_справа": k.справа.номер,
                "похожесть": round(k.похожесть, 2),
            }
            for k in отчёт.кандидаты[:200]
        ],
    }


@router.post("")
async def api_compare(
    файл1: UploadFile = File(...),
    файл2: UploadFile = File(...),
    лист1: str | None = None,
    лист2: str | None = None,
    user: auth.User = Depends(auth.current_user),
) -> dict:
    """Сверяет две таблицы и отдаёт сводку плюс имя файла отчёта."""
    слева = await _save(файл1)
    справа = await _save(файл2)
    try:
        результат = await asyncio.to_thread(_разобрать_и_сверить, слева, справа, лист1, лист2)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("Сверка не удалась: %s", e, exc_info=True)
        raise HTTPException(
            status_code=400,
            detail="Не удалось прочитать файлы. Проверьте, что это таблицы Excel и они не повреждены.",
        ) from e

    await asyncio.to_thread(ownership.claim, результат["файл"], user.login)
    return результат


@router.post("/alias")
async def api_confirm_alias(
    пара: Соответствие, user: auth.User = Depends(auth.current_user)
) -> dict:
    """«Это одна позиция» — записывает решение человека.

    Хранение целиком в `services/compare_memory`: там же живут ограничения,
    из-за которых пара может быть отвергнута (цепочка, пара сама на себя, тот
    же ключ уже сведён к другой позиции). Тексты ошибок оттуда идут человеку
    как есть — они написаны для бухгалтера, а не для программиста.
    """

    def _запомнить() -> int:
        return compare_memory.remember(
            normalize(пара.слева),
            normalize(пара.справа),
            name_from=пара.слева,
            name_to=пара.справа,
            by=user.login,
        )

    try:
        pair_id = await asyncio.to_thread(_запомнить)
    except compare_memory.ChainNotAllowed as e:
        # 409, а не 400: запрос правильный, мешает нынешнее состояние памяти,
        # и человеку предлагается сначала отменить прежнюю пару.
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"id": pair_id}


@router.delete("/alias")
async def api_forget_alias(отзыв: Отзыв, user: auth.User = Depends(auth.current_user)) -> dict:
    """Отмена подтверждения: ошибочная пара иначе тихо портит каждую сверку."""
    убрано = await asyncio.to_thread(compare_memory.forget, отзыв.id)
    if not убрано:
        raise HTTPException(status_code=404, detail="Такого соответствия не записано")
    return {"убрано": True}


@router.get("/aliases")
async def api_list_aliases(user: auth.User = Depends(auth.current_user)) -> list[dict]:
    """Что программа запомнила — человеческими написаниями, а не ключами."""
    return await asyncio.to_thread(compare_memory.list_pairs)
