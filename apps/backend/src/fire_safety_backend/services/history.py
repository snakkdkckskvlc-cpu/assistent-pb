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
            "(task_id, kind, status, created_at, finished_at, duration_sec, tokens, summary, error)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            ),
        )


def list_recent(limit: int = 50) -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT task_id, kind, status, created_at, finished_at, duration_sec, "
            "tokens, summary, error FROM task_history ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def clear() -> None:
    with connect() as conn:
        conn.execute("DELETE FROM task_history")
