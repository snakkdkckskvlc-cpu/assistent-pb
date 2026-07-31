"""Каждый видит только своё.

Это главный набор всей серверной части. На одном рабочем месте разграничение
было не нужно, а на общем сервере отсутствие проверки владельца означает, что
любой сотрудник читает договоры любого другого.

Проверяются три канала, каждый утекал по-своему:

1. `/api/tasks/{id}` — отдавал ПОЛНЫЙ результат: разбор договора вместе с
   текстом документа. Это шире, чем скачивание файла, и в ТЗ переезда этого
   пункта не было вовсе.
2. `/api/download/{имя}` — отдавал файл всякому, кто знает имя. Имена
   случайные, но это «защита незнанием»: ссылку пересылают в чате, она оседает
   в истории браузера и в логах прокси.
3. `/api/history` — показывал темы чужих писем и находки по чужим договорам.

Везде ожидается 404, а не 403: 403 подтверждает существование объекта.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from fire_safety_backend.infrastructure.queue import Task, queue
from fire_safety_backend.services import history, ownership

_OTHER = "коллега"


@pytest.fixture
def foreign_task() -> Task:
    """Задача коллеги, лежащая в той же очереди."""
    task = Task(id="чужая123456", kind="legal", owner=_OTHER, status="done")
    task.result = {"находки": [{"цитата_из_договора": "Штраф 40% от суммы договора"}]}
    queue._tasks[task.id] = task
    yield task
    queue._tasks.pop(task.id, None)


# --- Задачи ---


def test_foreign_task_is_not_readable(client: TestClient, foreign_task: Task) -> None:
    """Утечка шире, чем файл: здесь отдаётся разбор договора целиком."""
    r = client.get(f"/api/tasks/{foreign_task.id}")
    assert r.status_code == 404
    assert "Штраф 40%" not in r.text


def test_own_task_is_readable(client: TestClient, test_login: str) -> None:
    task = Task(id="своя12345678", kind="legal", owner=test_login, status="done")
    task.result = {"находки": []}
    queue._tasks[task.id] = task
    try:
        r = client.get(f"/api/tasks/{task.id}")
        assert r.status_code == 200
        assert r.json()["id"] == task.id
    finally:
        queue._tasks.pop(task.id, None)


def test_task_list_shows_only_own(client: TestClient, test_login: str, foreign_task: Task) -> None:
    mine = Task(id="моя987654321", kind="letter", owner=test_login)
    queue._tasks[mine.id] = mine
    try:
        ids = {t["id"] for t in client.get("/api/tasks").json()}
        assert mine.id in ids
        assert foreign_task.id not in ids
    finally:
        queue._tasks.pop(mine.id, None)


def test_feedback_cannot_harvest_foreign_result(client: TestClient, foreign_task: Task) -> None:
    """Иначе чужой task_id вытащил бы полный ответ модели по чужому договору
    в таблицу feedback.bad_output."""
    r = client.post(
        "/api/feedback",
        json={
            "function": "legal",
            "task_id": foreign_task.id,
            "rating": "down",
            "comment": "любопытство",
        },
    )
    assert r.status_code == 201  # сам отзыв принят
    from fire_safety_backend.infrastructure.db import connect

    with connect() as conn:
        row = conn.execute(
            "SELECT bad_output FROM feedback WHERE task_id = ?", (foreign_task.id,)
        ).fetchone()
    assert "Штраф 40%" not in (row["bad_output"] if row else "")


# --- Файлы ---


def test_foreign_file_is_not_downloadable(client: TestClient) -> None:
    from fire_safety_backend import config
    from fire_safety_backend.infrastructure import secure_files

    name = "письмо_коллеги.docx"
    secure_files.store(config.OUTPUT_DIR / name, "реквизиты и банковский счёт".encode())
    ownership.claim(name, _OTHER)

    r = client.get(f"/api/download/{name}")
    assert r.status_code == 404
    assert "банковский счёт" not in r.text


def test_own_file_is_downloadable(client: TestClient, test_login: str) -> None:
    from fire_safety_backend import config
    from fire_safety_backend.infrastructure import secure_files

    name = "моё_письмо.docx"
    payload = "моё содержимое".encode()
    secure_files.store(config.OUTPUT_DIR / name, payload)
    ownership.claim(name, test_login)

    r = client.get(f"/api/download/{name}")
    assert r.status_code == 200
    assert r.content == payload


def test_unclaimed_file_stays_readable(client: TestClient) -> None:
    """Документы, созданные до появления разграничения, не должны исчезнуть у
    своих же владельцев."""
    from fire_safety_backend import config
    from fire_safety_backend.infrastructure import secure_files

    name = "старый_документ.docx"
    secure_files.store(config.OUTPUT_DIR / name, b"x")
    assert client.get(f"/api/download/{name}").status_code == 200


def test_claim_is_idempotent(client: TestClient, test_login: str) -> None:
    """Повторная генерация того же имени не должна падать на первичном ключе.

    client нужен ради временной БД: без него запрос ушёл бы в боевую.
    """
    ownership.claim("повтор.docx", test_login)
    ownership.claim("повтор.docx", test_login)
    assert ownership.owner_of("повтор.docx") == test_login


# --- История ---


def test_history_shows_only_own(client: TestClient, test_login: str) -> None:
    history.record(Task(id="ч1", kind="letter", owner=_OTHER, status="done", finished_at=None))
    history.record(Task(id="м1", kind="letter", owner=test_login, status="done", finished_at=None))

    ids = {row["task_id"] for row in client.get("/api/history").json()}
    assert "м1" in ids
    assert "ч1" not in ids


def test_history_keeps_records_without_owner(client: TestClient) -> None:
    """Записи, сделанные до разграничения, видны всем — иначе у человека на
    глазах пропала бы собственная история."""
    history.record(Task(id="старая1", kind="legal", owner="", status="done", finished_at=None))
    ids = {row["task_id"] for row in client.get("/api/history").json()}
    assert "старая1" in ids


def test_clearing_history_does_not_touch_colleagues(client: TestClient, test_login: str) -> None:
    """На общем сервере кнопка «очистить историю» не должна стирать чужую работу."""
    history.record(Task(id="ч2", kind="legal", owner=_OTHER, status="done", finished_at=None))
    history.record(Task(id="м2", kind="legal", owner=test_login, status="done", finished_at=None))

    assert client.delete("/api/history").status_code == 204

    remaining = {row["task_id"] for row in history.list_recent(owner=_OTHER)}
    assert "ч2" in remaining
    assert "м2" not in remaining
