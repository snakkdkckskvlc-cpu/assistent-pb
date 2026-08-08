"""Сводка «что происходит»: как приложением реально пользуются.

`task_history` и `feedback` пишутся с самого начала, но их никто не смотрит.
Между тем это готовый ответ на вопросы, которые иначе решаются на глаз:
прижилась ли функция, где чаще жмут «палец вниз», сколько человек ждёт.

ЧЕГО ЗДЕСЬ НЕТ И БЫТЬ НЕ ДОЛЖНО — поля `summary` из `task_history`. В нём лежит
тема письма и число находок по конкретному договору, то есть содержание чужой
работы. В личной истории (`services/history.list_recent`) оно отдаётся только
владельцу, и сводка не имеет права обходить это разграничение с чёрного хода.
Здесь только счётчики.
"""

from __future__ import annotations

from ..infrastructure.db import connect

# Виды задач по-русски. Ключи — те же, что пишет очередь в task_history.kind.
KIND_NAMES = {
    "spellcheck": "Проверка документа",
    "legal": "Анализ договора",
    "letter": "Письмо",
    "ask": "Вопрос по документу",
    "batch": "Пакетная проверка",
}


def _median(values: list[float]) -> float | None:
    """Медиана, а не среднее.

    Та же причина, что в history.typical_duration: один договор на сорок
    страниц сдвигает среднее так, что оно перестаёт описывать типичное
    ожидание. Руководителю нужно «обычно столько», а не «в среднем по палате».
    """
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def _percentile90(values: list[float]) -> float | None:
    """Девяностый перцентиль — «а как бывает плохо».

    Без него медиана обманчива: если половина договоров разбирается за минуту,
    а каждый десятый за двадцать, по одной медиане кажется, что всё хорошо, а
    жалуются как раз те, кто попал в хвост.
    """
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, round(0.9 * (len(ordered) - 1))))
    return float(ordered[idx])


def _by_kind(conn, days: int) -> list[dict]:
    rows = conn.execute(
        "SELECT kind, status, duration_sec FROM task_history "
        "WHERE datetime(created_at) >= datetime('now', ?)",
        (f"-{days} days",),
    ).fetchall()

    buckets: dict[str, dict] = {}
    for r in rows:
        b = buckets.setdefault(
            r["kind"], {"вид": r["kind"], "всего": 0, "удачных": 0, "неудачных": 0, "_сек": []}
        )
        b["всего"] += 1
        if r["status"] == "done":
            b["удачных"] += 1
            # Длительность берём только у удачных: упавшая через две секунды
            # задача занизила бы типичное время ожидания, хотя пользователь
            # в это время не ждал, а получал ошибку.
            if r["duration_sec"] is not None:
                b["_сек"].append(float(r["duration_sec"]))
        else:
            b["неудачных"] += 1

    out = []
    for b in buckets.values():
        secs = b.pop("_сек")
        b["название"] = KIND_NAMES.get(b["вид"], b["вид"])
        b["медиана_сек"] = _median(secs)
        b["девяностый_сек"] = _percentile90(secs)
        out.append(b)
    out.sort(key=lambda b: b["всего"], reverse=True)
    return out


def _by_day(conn, days: int) -> list[dict]:
    """Задач по дням — для полоски активности.

    Дни без задач возвращаются нулями: без них график врёт, схлопывая
    выходные и превращая простой в ровную линию.
    """
    rows = conn.execute(
        "SELECT date(created_at) AS д, COUNT(*) AS n FROM task_history "
        "WHERE datetime(created_at) >= datetime('now', ?) GROUP BY д",
        (f"-{days} days",),
    ).fetchall()
    known = {r["д"]: r["n"] for r in rows}

    filled = conn.execute(
        "WITH RECURSIVE d(x) AS ("
        "  SELECT date('now', ?) "
        "  UNION ALL SELECT date(x, '+1 day') FROM d WHERE x < date('now')"
        ") SELECT x FROM d",
        (f"-{days} days",),
    ).fetchall()
    return [{"дата": r["x"], "всего": known.get(r["x"], 0)} for r in filled]


def _ratings(conn, days: int) -> list[dict]:
    rows = conn.execute(
        "SELECT function, rating, COUNT(*) AS n FROM feedback "
        "WHERE datetime(created_at) >= datetime('now', ?) "
        "GROUP BY function, rating",
        (f"-{days} days",),
    ).fetchall()
    buckets: dict[str, dict] = {}
    for r in rows:
        b = buckets.setdefault(
            r["function"],
            {
                "функция": r["function"],
                "название": KIND_NAMES.get(r["function"], r["function"]),
                "вверх": 0,
                "вниз": 0,
            },
        )
        b["вверх" if r["rating"] == "up" else "вниз"] += r["n"]
    out = list(buckets.values())
    # Сначала то, где недовольны чаще: экран существует, чтобы находить
    # проблемы, а не любоваться удачами.
    out.sort(key=lambda b: (-b["вниз"], -b["вверх"]))
    return out


def _by_person(conn, days: int) -> list[dict]:
    """Кто пользуется. Только счётчик, без содержания задач.

    Отдаётся администратору: владельцу нужно видеть, прижилось ли приложение,
    а «прижилось» — это сколько людей им пользуются, а не сколько задач всего.
    Записи без владельца (сделанные до разграничения доступа) идут отдельной
    строкой, а не приписываются кому-то.
    """
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(owner, ''), '—') AS кто, COUNT(*) AS n "
        "FROM task_history WHERE datetime(created_at) >= datetime('now', ?) "
        "GROUP BY кто ORDER BY n DESC",
        (f"-{days} days",),
    ).fetchall()
    return [{"кто": r["кто"], "всего": r["n"]} for r in rows]


def collect(days: int = 30, *, with_people: bool = False) -> dict:
    """Сводка за последние `days` дней.

    `with_people` включает разбивку по сотрудникам — вызывающий обязан сам
    проверить права. Разделено намеренно: разбивка по людям это наблюдение за
    сотрудниками, и включаться она должна осознанно, а не потому что данные
    оказались под рукой.
    """
    with connect() as conn:
        kinds = _by_kind(conn, days)
        data = {
            "период_дней": days,
            "всего_задач": sum(k["всего"] for k in kinds),
            "неудачных": sum(k["неудачных"] for k in kinds),
            "по_видам": kinds,
            "по_дням": _by_day(conn, days),
            "оценки": _ratings(conn, days),
        }
        if with_people:
            data["по_людям"] = _by_person(conn, days)
    return data
