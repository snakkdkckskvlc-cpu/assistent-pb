"""Сервис истории задач: запись завершённых задач и выдача списка.

Пишется автоматически из воркера очереди (infrastructure/queue.py::
on_task_finished, подключается в lifespan main.py). Полный result задачи
НЕ сохраняется — он бывает большим и содержит текст документов; в историю
идёт короткая сводка («Ошибок: 5», тема письма) + тайминги.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from ..infrastructure.db import connect

if TYPE_CHECKING:
    from ..infrastructure.queue import Task


def _summarize(task: Task) -> str:
    result = task.result if isinstance(task.result, dict) else {}
    if task.status != "done":
        return ""
    if task.kind == "spellcheck":
        total = (result.get("stats") or {}).get("total_errors")
        return f"Ошибок: {total}" if total is not None else ""
    if task.kind == "legal":
        findings = result.get("находки")
        return f"Находок: {len(findings)}" if isinstance(findings, list) else ""
    if task.kind == "letter":
        return str(result.get("тема") or "")[:200]
    if task.kind == "batch":
        stats = result.get("stats") or {}
        return f"Файлов: {stats.get('всего', '?')}, договоров: {stats.get('договоров', '?')}"
    return ""


def record(task: Task) -> None:
    duration = None
    if task.started_at and task.finished_at:
        duration = (
            datetime.fromisoformat(task.finished_at) - datetime.fromisoformat(task.started_at)
        ).total_seconds()
    with connect() as conn:
        conn.execute(
            "INSERT INTO task_history "
            "(task_id, kind, status, created_at, finished_at, duration_sec, tokens, "
            "summary, error, owner)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.id,
                task.kind,
                task.status,
                task.created_at,
                task.finished_at,
                duration,
                task.tokens,
                _summarize(task),
                task.error,
                getattr(task, "owner", ""),
            ),
        )


def list_recent(limit: int = 50, owner: str | None = None) -> list[dict]:
    """История задач. С owner — только свои плюс записи без владельца.

    Записи без владельца — сделанные до появления разграничения доступа.
    Прятать их значило бы, что у человека на глазах пропала собственная
    история.
    """
    sql = (
        "SELECT task_id, kind, status, created_at, finished_at, duration_sec, "
        "tokens, summary, error, owner FROM task_history"
    )
    params: list = []
    if owner is not None:
        sql += " WHERE owner = ? OR owner = ''"
        params.append(owner)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def clear(owner: str | None = None) -> None:
    """Чистит историю. С owner — только свою: на общем сервере кнопка
    «очистить историю» не должна стирать работу коллег."""
    with connect() as conn:
        if owner is None:
            conn.execute("DELETE FROM task_history")
        else:
            conn.execute("DELETE FROM task_history WHERE owner = ?", (owner,))


def typical_duration(kind: str, limit: int = 10) -> float | None:
    """Медиана длительности последних успешных задач этого вида.

    Нужна для честной оценки ожидания в очереди. Медиана, а не среднее: один
    договор на 40 страниц иначе сдвинул бы оценку для всех последующих.
    None — статистики ещё нет.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT duration_sec FROM task_history "
            "WHERE kind = ? AND status = 'done' AND duration_sec IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (kind, limit),
        ).fetchall()
    values = sorted(r["duration_sec"] for r in rows)
    if not values:
        return None
    middle = len(values) // 2
    if len(values) % 2:
        return float(values[middle])
    return (values[middle - 1] + values[middle]) / 2
