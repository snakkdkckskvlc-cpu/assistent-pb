"""Сводка «что происходит».

Главное, что здесь проверяется, — не арифметика, а ДВА разграничения.

Первое: в сводку не имеет права попасть поле `summary` из task_history. В нём
лежит тема письма и число находок по конкретному договору, и в личной истории
оно отдаётся только владельцу. Сводка не должна обходить это с чёрного хода.

Второе: разбивку по сотрудникам видит только администратор. Для остальных это
наблюдение за коллегами, а не рабочая информация.

Обе ошибки тихие: цифры на экране выглядели бы правильно в любом случае.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fire_safety_backend.infrastructure import db as db_module
from fire_safety_backend.services import stats

SECRET_SUMMARY = "Тема: коммерческое предложение по объекту №14"


def _ago(days: float) -> str:
    return (datetime.now() - timedelta(days=days)).isoformat(sep=" ", timespec="seconds")


def _task(
    conn,
    kind: str,
    status: str = "done",
    *,
    sec: float | None = 10.0,
    days: float = 1.0,
    owner: str = "ivanov",
    summary: str = "",
) -> None:
    conn.execute(
        "INSERT INTO task_history (task_id, kind, status, created_at, finished_at, "
        "duration_sec, tokens, summary, error, owner) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            f"t{days}{kind}{owner}{sec}",
            kind,
            status,
            _ago(days),
            _ago(days),
            sec,
            0,
            summary,
            None,
            owner,
        ),
    )


def _vote(conn, function: str, rating: str, days: float = 1.0) -> None:
    conn.execute(
        "INSERT INTO feedback (created_at, function, task_id, rating, comment) VALUES (?,?,?,?,?)",
        (_ago(days), function, "t1", rating, ""),
    )


@pytest.fixture
def seeded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "stats.db")
    db_module.init_db()
    with db_module.connect() as conn:
        # Проверка документа: три удачных с разбросом длительностей и одна упавшая.
        for sec in (2.0, 6.0, 100.0):
            _task(conn, "spellcheck", sec=sec, days=1)
        _task(conn, "spellcheck", status="error", sec=0.5, days=1, owner="petrov")
        # Отменённая: человек нажал «Отменить». Это НЕ отказ, и считаться
        # вместе с падениями она не должна — иначе сводка краснеет от
        # обычного рабочего действия.
        _task(conn, "spellcheck", status="cancelled", sec=1.0, days=1, owner="petrov")
        # Анализ договора: один удачный, с секретной сводкой в записи.
        _task(conn, "legal", sec=50.0, days=2, owner="petrov", summary=SECRET_SUMMARY)
        # Старое — за окном в 7 дней, но внутри 30.
        _task(conn, "letter", sec=3.0, days=20, owner="ivanov")

        _vote(conn, "legal", "down")
        _vote(conn, "legal", "down")
        _vote(conn, "legal", "up")
        _vote(conn, "spellcheck", "up")


# --- разграничения, ради которых этот файл и написан ---


def test_summary_never_leaks_into_stats(seeded: None) -> None:
    """Содержание чужой работы не должно просочиться в общую сводку."""
    data = stats.collect(30, with_people=True)
    assert SECRET_SUMMARY not in repr(data)


def test_people_breakdown_is_opt_in(seeded: None) -> None:
    assert "по_людям" not in stats.collect(30)
    assert "по_людям" in stats.collect(30, with_people=True)


def test_ordinary_user_gets_no_people_breakdown(client: TestClient) -> None:
    """Тестовая учётка создаётся без прав администратора."""
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert "по_людям" not in r.json()


def test_admin_gets_people_breakdown(client: TestClient) -> None:
    from fire_safety_backend.services import auth as auth_service

    auth_service.create_user("nachalnik", is_admin=True)
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"login": "nachalnik"}).status_code == 200
    r = client.get("/api/stats")
    assert r.status_code == 200
    assert "по_людям" in r.json()


def test_stats_require_login(anon_client: TestClient) -> None:
    assert anon_client.get("/api/stats").status_code == 401


# --- счёт ---


def test_counts_by_kind(seeded: None) -> None:
    data = stats.collect(30)
    by_kind = {k["вид"]: k for k in data["по_видам"]}
    assert by_kind["spellcheck"]["всего"] == 5
    assert by_kind["spellcheck"]["удачных"] == 3
    assert by_kind["spellcheck"]["неудачных"] == 1
    assert by_kind["spellcheck"]["отменённых"] == 1
    assert data["всего_задач"] == 7
    assert data["неудачных"] == 1
    assert data["отменённых"] == 1


def test_cancelled_is_not_a_failure(seeded: None) -> None:
    """Отмену нажимает сам человек: ошибся файлом, передумал, освободил очередь
    коллеге. Пока отменённые считались вместе с упавшими, сводка красилась в
    тревожный цвет от обычного рабочего действия — а сигнал, срабатывающий на
    норму, перестают читать вовсе.

    Проверяется именно РАЗДЕЛЕНИЕ, а не число: сложить их обратно в одну
    колонку — самый лёгкий способ незаметно вернуть прежнее поведение.
    """
    data = stats.collect(30)
    отменённых = data["отменённых"]
    неудачных = data["неудачных"]
    assert отменённых == 1
    assert неудачных == 1, "отменённая задача просочилась в счёт отказов"
    by_kind = {k["вид"]: k for k in data["по_видам"]}
    сп = by_kind["spellcheck"]
    assert сп["всего"] == сп["удачных"] + сп["неудачных"] + сп["отменённых"], (
        "запуски перестали сходиться: какая-то задача не попала ни в одну колонку"
    )


def test_cancelled_task_duration_excluded(seeded: None) -> None:
    """Отменённая задача не ждала до конца, и её длительность не говорит
    ничего о типичном ожидании. В расчёт идут только дошедшие."""
    by_kind = {k["вид"]: k for k in stats.collect(30)["по_видам"]}
    # Медиана по 2, 6, 100 — это 6. Попади туда отменённая с 1.0, стало бы 4.
    assert by_kind["spellcheck"]["медиана_сек"] == 6


def test_failed_task_duration_excluded(seeded: None) -> None:
    """Упавшая через полсекунды задача не должна занижать типичное ожидание:
    пользователь в это время не ждал, а получал ошибку."""
    by_kind = {k["вид"]: k for k in stats.collect(30)["по_видам"]}
    # медиана по 2, 6, 100 — это 6; если бы попала 0.5, стало бы 4
    assert by_kind["spellcheck"]["медиана_сек"] == 6.0


def test_percentile_shows_the_tail(seeded: None) -> None:
    """Медиана 6 секунд скрывает, что каждый десятый ждёт сто."""
    by_kind = {k["вид"]: k for k in stats.collect(30)["по_видам"]}
    assert by_kind["spellcheck"]["девяностый_сек"] == 100.0


def test_window_filters_old_tasks(seeded: None) -> None:
    """Письмо двадцатидневной давности попадает в 30 дней и не попадает в 7."""
    kinds30 = {k["вид"] for k in stats.collect(30)["по_видам"]}
    kinds7 = {k["вид"] for k in stats.collect(7)["по_видам"]}
    assert "letter" in kinds30
    assert "letter" not in kinds7


def test_days_are_filled_with_zeros(seeded: None) -> None:
    """Дни без задач возвращаются нулями: иначе график схлопывает выходные
    и простой выглядит ровной линией."""
    days = stats.collect(7)["по_дням"]
    assert len(days) >= 7
    assert any(d["всего"] == 0 for d in days)
    assert [d["дата"] for d in days] == sorted(d["дата"] for d in days)


def test_ratings_worst_first(seeded: None) -> None:
    """Экран существует, чтобы находить проблемы, — недовольство сверху."""
    ratings = stats.collect(30)["оценки"]
    assert ratings[0]["функция"] == "legal"
    assert ratings[0]["вниз"] == 2
    assert ratings[0]["вверх"] == 1


def test_kind_names_are_human(seeded: None) -> None:
    by_kind = {k["вид"]: k for k in stats.collect(30)["по_видам"]}
    assert by_kind["legal"]["название"] == "Анализ договора"


def test_unknown_window_falls_back(client: TestClient) -> None:
    """days приходит из браузера и уходит в SQL модификатором даты —
    произвольное значение туда не пускаем."""
    assert client.get("/api/stats", params={"days": 999}).json()["период_дней"] == 30
    assert client.get("/api/stats", params={"days": 7}).json()["период_дней"] == 7


def test_empty_database_does_not_crash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Первый день после установки: задач ещё нет, экран обязан открыться."""
    monkeypatch.setattr(db_module, "DB_PATH", tmp_path / "empty.db")
    db_module.init_db()
    data = stats.collect(30, with_people=True)
    assert data["всего_задач"] == 0
    assert data["по_видам"] == []
    assert data["по_дням"]
