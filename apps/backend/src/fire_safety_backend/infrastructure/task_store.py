"""Результаты задач, переживающие перезапуск сервера.

Очередь держит задачи в памяти процесса (queue.py::_tasks). На одном рабочем
месте это было терпимо: перезапускал сам пользователь, зная, что делает. На
сервере перезапуск — это обновление, перезагрузка Windows или падение службы,
и вместе с ним пропадал бы готовый разбор договора, которого человек ждал
минутами и ещё не успел скачать.

### Чего этот модуль НЕ делает

Не возобновляет прерванные задачи. Работа модели нигде не сохраняется, и
продолжить с середины нельзя. Всё, что осталось в состоянии «в очереди» или
«выполняется», при старте честно помечается ошибкой: делать вид, что задача
жива, хуже — человек будет ждать ответа, который никогда не придёт.

### Почему результат шифруется

`result` — это разбор договора вместе с текстом документа. В историю задач
(services/history.py) текст намеренно не пишется: там только короткая сводка.
Складывая полный результат в app.db открытым, мы бы превратили базу в
хранилище договоров и обесценили шифрование файлов на диске. Поэтому блоб
проходит через тот же DPAPI-слой (secure_files).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from . import secure_files
from .db import connect
from .queue import Task

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)

INTERRUPTED_ERROR = "Задача прервана перезапуском сервера"


def _encode(result: object) -> bytes | None:
    if result is None:
        return None
    raw = json.dumps(result, ensure_ascii=False).encode("utf-8")
    return secure_files.encrypt_blob(raw)


def _decode(blob: bytes | None) -> object:
    if not blob:
        return None
    try:
        return json.loads(secure_files.decrypt_blob(bytes(blob)).decode("utf-8"))
    except Exception as e:
        # Чаще всего это блоб, зашифрованный другой учётной записью Windows
        # (папку data/ перенесли). Терять из-за этого весь ответ на запрос
        # статуса нельзя — отдаём задачу без результата.
        log.warning("Не удалось прочитать сохранённый результат: %s", e)
        return None


def save(task: Task) -> None:
    """Сохраняет задачу. Отказ хранилища не должен ронять саму задачу.

    Если шифрование недоступно, НЕ сохраняем: лучше потерять результат при
    перезапуске, чем положить текст договора в базу открытым.
    """
    status = secure_files.status()
    if status.broken:
        log.warning("Результат задачи %s не сохранён: шифрование недоступно", task.id)
        return
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO task_results "
                "(task_id, owner, kind, status, result, error, progress, percent, "
                " created_at, started_at, finished_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(task_id) DO UPDATE SET "
                "  status = excluded.status, result = excluded.result, "
                "  error = excluded.error, progress = excluded.progress, "
                "  percent = excluded.percent, finished_at = excluded.finished_at",
                (
                    task.id,
                    task.owner,
                    task.kind,
                    task.status,
                    _encode(task.result),
                    task.error,
                    task.progress,
                    task.percent,
                    task.created_at,
                    task.started_at,
                    task.finished_at,
                ),
            )
    except Exception:
        # Задача уже посчитана; уронить ответ пользователю из-за проблемы с
        # сохранением было бы обиднее всего.
        log.exception("Не удалось сохранить результат задачи %s", task.id)


def load(task_id: str, owner: str | None = None) -> Task | None:
    """Задача из базы. С owner отдаёт только свою — как queue.get()."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM task_results WHERE task_id = ?", (task_id,)).fetchone()
    if row is None:
        return None
    if owner is not None and row["owner"] != owner and row["owner"] != "":
        return None
    return Task(
        id=row["task_id"],
        kind=row["kind"],
        owner=row["owner"],
        status=row["status"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        progress=row["progress"],
        result=_decode(row["result"]),
        error=row["error"],
        percent=row["percent"],
    )


def mark_interrupted() -> int:
    """При старте: всё незавершённое помечается ошибкой. Возвращает сколько.

    Возобновить нельзя — работа модели не сохраняется. Оставить как есть тоже
    нельзя: задача навсегда осталась бы «в очереди», и человек ждал бы ответа,
    которого не будет.
    """
    with connect() as conn:
        cur = conn.execute(
            "UPDATE task_results SET status = 'error', error = ? "
            "WHERE status IN ('queued', 'running')",
            (INTERRUPTED_ERROR,),
        )
        return cur.rowcount


def purge_older_than(cutoff_iso: str) -> int:
    """Чистит старые результаты. Срок общий с файлами (DATA_RETENTION_DAYS):
    хранить разбор договора в базе дольше, чем сам договор на диске, незачем."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM task_results WHERE created_at < ?", (cutoff_iso,))
        return cur.rowcount


def owned_ids(owner: str) -> Iterable[str]:
    with connect() as conn:
        rows = conn.execute("SELECT task_id FROM task_results WHERE owner = ?", (owner,)).fetchall()
    return [r["task_id"] for r in rows]
