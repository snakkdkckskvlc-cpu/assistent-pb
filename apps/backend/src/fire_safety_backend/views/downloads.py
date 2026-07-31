"""Роутер: скачивание сгенерированных файлов.

Отдаётся не файл с диска, а расшифрованное содержимое: в data/outputs
документы лежат зашифрованными (infrastructure/secure_files.py). Для клиента
ничего не меняется — он по-прежнему запрашивает `/api/download/<имя файла>`
без всяких `.enc`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .. import config
from ..infrastructure import secure_files
from ..services import ownership
from . import auth

router = APIRouter(prefix="/api/download", tags=["download"])

_MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _content_disposition(filename: str) -> str:
    """Заголовок с именем файла, корректный для кириллицы.

    FileResponse делал это сам, а у ручного ответа заголовок надо собрать:
    в `filename=` можно только ASCII, поэтому не-ASCII имя (а у нас
    `документ_исправленный.docx`) уходит через `filename*` по RFC 5987.
    Иначе пользователь получает файл с искажённым именем.
    """
    quoted = quote(filename)
    if quoted != filename:
        return f"attachment; filename*=utf-8''{quoted}"
    return f'attachment; filename="{filename}"'


@router.get("/{filename}")
async def api_download(filename: str, user: auth.User = Depends(auth.current_user)) -> Response:
    safe = Path(filename).name  # защита от path traversal
    # Случайное имя (uuid) — это «защита незнанием»: ссылку пересылают в чате,
    # она оседает в истории браузера и в логах прокси. На общем сервере этого
    # мало. Чужой файл отдаёт 404, а не 403 — иначе ответ подтверждает, что
    # файл с таким именем существует.
    if not await asyncio.to_thread(ownership.may_read, safe, user.login):
        raise HTTPException(status_code=404, detail="Файл не найден")
    logical = config.OUTPUT_DIR / safe
    try:
        # Чтение с диска и расшифровка — блокирующие, уводим с event loop.
        payload = await asyncio.to_thread(secure_files.load, logical)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail="Файл не найден") from e
    except secure_files.DecryptError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    media_type = _MEDIA_TYPES.get(Path(safe).suffix.lower(), "application/octet-stream")
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Content-Disposition": _content_disposition(safe)},
    )
