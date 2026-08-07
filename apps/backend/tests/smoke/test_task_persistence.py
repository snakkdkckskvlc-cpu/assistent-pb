"""Результаты задач переживают перезапуск сервера.

На одном рабочем месте потеря очереди при перезапуске была терпима:
перезапускал сам пользователь, зная, что делает. На сервере перезапуск — это
обновление, перезагрузка Windows или падение службы, и вместе с ним пропадал
бы готовый разбор договора, которого человек ждал минутами и не успел скачать.

Главный тест здесь — `test_result_in_the_database_is_encrypted`. Складывая
полный результат в app.db, мы кладём туда текст договоров: в task_history он
намеренно НЕ пишется (только сводка), и обесценивать этим шифрование файлов
на диске нельзя.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Модулем, а не `from ... import DB_PATH`: фикстура подменяет путь к базе
# атрибутом модуля (tests/conftest.py), а импорт значения связал бы его ДО
# подмены. Тест тогда читал бы настоящую data/app.db — на CI её нет вовсе
# (FileNotFoundError), а локально она есть, секрета в ней нет, и проверка
# проходила вхолостую, ничего не проверяя.
from fire_safety_backend.infrastructure import db as db_module
from fire_safety_backend.infrastructure import secure_files, task_store
from fire_safety_backend.infrastructure.queue import Task, queue

_SECRET = "Цена договора 4 500 000 рублей, штраф 40% при просрочке"


def _done_task(task_id: str, owner: str, result: dict | None = None) -> Task:
    return Task(
        id=task_id,
        kind="legal",
        owner=owner,
        status="done",
        result=result if result is not None else {"находки": [{"цитата": _SECRET}]},
        finished_at="2026-07-31T10:00:00+00:00",
    )


# --- Сохранение и чтение ---


def test_result_survives_when_queue_forgets(client: TestClient, test_login: str) -> None:
    """Ровно то, ради чего всё затевалось: перезапуск не теряет готовый разбор."""
    task_store.save(_done_task("сохранён1", test_login))
    queue._tasks.pop("сохранён1", None)  # как после перезапуска

    r = client.get("/api/tasks/сохранён1")

    assert r.status_code == 200
    assert r.json()["status"] == "done"
    assert _SECRET in r.text


def test_foreign_saved_result_is_not_readable(client: TestClient) -> None:
    """Разграничение обязано работать и на пути через базу, а не только по
    памяти — иначе перезапуск снимал бы защиту."""
    task_store.save(_done_task("чужой1", "коллега"))
    queue._tasks.pop("чужой1", None)

    r = client.get("/api/tasks/чужой1")

    assert r.status_code == 404
    assert _SECRET not in r.text


def test_saved_task_keeps_its_fields(client: TestClient, test_login: str) -> None:
    task_store.save(_done_task("поля1", test_login))
    loaded = task_store.load("поля1", test_login)
    assert loaded is not None
    assert loaded.kind == "legal"
    assert loaded.status == "done"
    assert loaded.owner == test_login


def test_saving_twice_updates_instead_of_failing(client: TestClient, test_login: str) -> None:
    """Задача сохраняется по ходу и в конце — первичный ключ падать не должен."""
    task_store.save(_done_task("повтор1", test_login, {"этап": "первый"}))
    task_store.save(_done_task("повтор1", test_login, {"этап": "второй"}))
    assert task_store.load("повтор1", test_login).result == {"этап": "второй"}


# --- Шифрование результата ---


def test_result_in_the_database_is_encrypted(
    client: TestClient, test_login: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главный тест: текста договора в файле базы быть не должно.

    Иначе app.db становится хранилищем договоров открытым текстом, и
    шифрование файлов на диске теряет смысл.
    """

    class _Xor:
        name = "xor"

        def protect(self, data: bytes) -> bytes:
            return bytes(b ^ 0x5A for b in data)

        def unprotect(self, data: bytes) -> bytes:
            return bytes(b ^ 0x5A for b in data)

    secure_files.use_protector(_Xor())
    try:
        task_store.save(_done_task("шифр1", test_login))
        raw = Path(db_module.DB_PATH).read_bytes()
        assert _SECRET.encode() not in raw
        assert b"4 500 000" not in raw
        # И при этом читается обратно.
        assert _SECRET in str(task_store.load("шифр1", test_login).result)
    finally:
        secure_files.reset()


@pytest.mark.skipif(
    secure_files.protector() is None,
    reason=(
        "на этой платформе файлы хранятся открытым текстом (DPAPI только под "
        "Windows), поэтому «нечитаемый блоб» не воспроизводится: подмена "
        "протектора после сохранения ни на что не влияет"
    ),
)
def test_unreadable_blob_does_not_break_the_answer(client: TestClient, test_login: str) -> None:
    """Блоб, зашифрованный другой учётной записью, не должен ронять весь
    ответ — отдаём задачу без результата."""

    class _Xor:
        name = "xor"

        def protect(self, data: bytes) -> bytes:
            return bytes(b ^ 0x5A for b in data)

        def unprotect(self, data: bytes) -> bytes:
            raise OSError("не та учётная запись")

    task_store.save(_done_task("битый1", test_login))
    secure_files.use_protector(_Xor())
    try:
        loaded = task_store.load("битый1", test_login)
        assert loaded is not None
        assert loaded.result is None
    finally:
        secure_files.reset()


def test_nothing_is_saved_when_encryption_is_broken(client: TestClient, test_login: str) -> None:
    """Лучше потерять результат при перезапуске, чем положить текст договора
    в базу открытым."""
    secure_files.use_protector(None, broken=True)
    try:
        task_store.save(_done_task("несохранён1", test_login))
        assert task_store.load("несохранён1", test_login) is None
    finally:
        secure_files.reset()


# --- Прерванные задачи ---


def test_interrupted_tasks_are_marked_not_left_hanging(client: TestClient, test_login: str) -> None:
    """Возобновить нельзя — работа модели не сохраняется. Но оставить «в
    очереди» навсегда тоже нельзя: человек ждал бы ответа, которого не будет."""
    running = _done_task("висящая1", test_login)
    running.status = "running"
    task_store.save(running)

    assert task_store.mark_interrupted() >= 1

    loaded = task_store.load("висящая1", test_login)
    assert loaded.status == "error"
    assert loaded.error == task_store.INTERRUPTED_ERROR


def test_finished_tasks_are_untouched_by_restart(client: TestClient, test_login: str) -> None:
    task_store.save(_done_task("готовая1", test_login))
    task_store.mark_interrupted()
    assert task_store.load("готовая1", test_login).status == "done"


# --- Память очереди ---


def test_queue_evicts_only_finished_tasks() -> None:
    """Сервер живёт неделями. Вытеснять безопасно — результат в базе; но
    выкинуть ждущую задачу значило бы потерять её прямо во время работы."""
    from fire_safety_backend.infrastructure import queue as queue_module

    q = queue_module.TaskQueue()
    monkey_limit = 3
    original = queue_module._MAX_TASKS_IN_MEMORY
    queue_module._MAX_TASKS_IN_MEMORY = monkey_limit
    try:
        for i in range(5):
            t = Task(id=f"old{i}", kind="legal", status="done", created_at=f"2026-01-0{i + 1}")
            q._tasks[t.id] = t
        waiting = Task(id="ждёт", kind="legal", status="queued", created_at="2026-01-01")
        q._tasks[waiting.id] = waiting

        q._evict_finished()

        assert "ждёт" in q._tasks, "ждущую задачу вытеснять нельзя"
        assert len(q._tasks) <= monkey_limit + 1
    finally:
        queue_module._MAX_TASKS_IN_MEMORY = original
