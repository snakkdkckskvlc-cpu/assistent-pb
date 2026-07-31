"""Кто чей файл: привязка результатов к сотруднику.

До переезда на сервер `/api/download/<имя>` отдавал любой файл из data/outputs
всякому, кто знает имя. Имена случайные (uuid), но это «защита незнанием»:
ссылку пересылают в чате, она попадает в историю браузера и в логи прокси — и
чужой разбор договора открывается. В однопользовательском десктопе это ничего
не значило, на общем сервере значит.

Отдельная таблица, а не колонка в task_history: файл появляется в середине
задачи (генератор сохраняет DOCX), а запись в историю делается ПОСЛЕ её
завершения. Ждать конца задачи, чтобы узнать владельца уже созданного файла,
неправильно.
"""

from __future__ import annotations

import logging

from ..infrastructure.db import connect

log = logging.getLogger(__name__)


def claim(filename: str, owner: str) -> None:
    """Записывает владельца файла. Без владельца (десктопный режим) — не пишет.

    Идемпотентно: имя файла — первичный ключ, повторная генерация того же
    имени просто обновляет запись.
    """
    if not owner or not filename:
        return
    with connect() as conn:
        conn.execute(
            "INSERT INTO output_files (filename, owner) VALUES (?, ?) "
            "ON CONFLICT(filename) DO UPDATE SET owner = excluded.owner",
            (filename, owner),
        )


def owner_of(filename: str) -> str | None:
    """Владелец файла или None, если файл никем не заявлен."""
    with connect() as conn:
        row = conn.execute(
            "SELECT owner FROM output_files WHERE filename = ?", (filename,)
        ).fetchone()
    return row["owner"] if row else None


def may_read(filename: str, owner: str) -> bool:
    """Можно ли этому сотруднику забрать файл.

    Незаявленный файл доступен всем вошедшим — иначе документы, созданные до
    появления разграничения, перестали бы скачиваться у своих же владельцев.
    Заявленный отдаётся только владельцу.
    """
    actual = owner_of(filename)
    return actual is None or actual == owner


def forget(filename: str) -> None:
    """Снимает заявку — вызывается при удалении файла автоочисткой, чтобы
    таблица не росла вечно вслед за уже несуществующими файлами."""
    with connect() as conn:
        conn.execute("DELETE FROM output_files WHERE filename = ?", (filename,))
