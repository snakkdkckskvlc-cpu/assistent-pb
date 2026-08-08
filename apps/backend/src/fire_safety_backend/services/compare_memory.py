"""Память подтверждённых соответствий для сверки таблиц.

Зачем. Нормализация (`table_compare.normalize`) намеренно не умеет догадок:
«Труба 25» и «Труба 32» отличаются одним символом, и любое «умное» приведение
сделало бы их одной позицией. Поэтому варианты написания одной и той же позиции
она не склеивает, а показывает человеку кандидатами. Подтверждённая пара
запоминается здесь — со второго раза сопоставление идёт молча.

Память ОБЩАЯ на компанию, а не личная: номенклатура одна на всех, и заставлять
каждого подтверждать «кабель» заново значит не сделать ничего. Автор пары
записан — по нему разбираются, если пара окажется ошибочной.

Главная опасность и главное правило. Ошибочное подтверждение молча склеивает
две РАЗНЫЕ позиции навсегда, и отчёт скажет «всё сошлось» там, где не сошлось —
ровно тот отказ, ради которого в движке выбран принцип «лучше не сопоставить,
чем сопоставить неверно». Отсюда три ограничения, и они не косметические:

  1. пары видны списком и отзываются (`list_pairs`, `forget`);
  2. цепочки запрещены (`A→B` плюс `B→C`) — иначе результат зависит от порядка
     применения, а объяснить его человеку нечем;
  3. пара сама на себя не заводится — это молчаливый способ ничего не сделать.

Движок принимает готовый словарь параметром `синонимы`, поэтому здесь только
хранение: сервис ничего не сопоставляет сам.
"""

from __future__ import annotations

import sqlite3

from ..infrastructure.db import connect


class ChainNotAllowed(ValueError):
    """Пара образовала бы цепочку соответствий."""


def synonyms() -> dict[str, str]:
    """Готовый словарь для `table_compare.compare(синонимы=...)`."""
    with connect() as conn:
        rows = conn.execute("SELECT key_from, key_to FROM compare_synonym").fetchall()
    return {r["key_from"]: r["key_to"] for r in rows}


def list_pairs() -> list[dict]:
    """Что человек подтвердил — в порядке от новых к старым.

    Отдаётся с исходными написаниями: по нормализованному ключу
    («кабель ввгнг 3x1.5») человек свою позицию не узнает.
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, key_from, key_to, name_from, name_to, confirmed_by, created_at "
            "FROM compare_synonym ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def remember(
    key_from: str, key_to: str, *, name_from: str = "", name_to: str = "", by: str = ""
) -> int:
    """Запоминает пару. Возвращает id записи.

    Повторное подтверждение той же пары не ошибка: человек мог забыть, что уже
    подтверждал, и падать на этом значило бы наказывать за забывчивость.
    А вот подтверждение того же ключа на ДРУГОЙ — ошибка: молча переписать
    прежнее соответствие нельзя, старые сверки после этого поедут.
    """
    key_from, key_to = key_from.strip(), key_to.strip()
    if not key_from or not key_to:
        raise ValueError("Пустой ключ — нечего запоминать")
    if key_from == key_to:
        raise ValueError("Позиция и так совпадает сама с собой")

    with connect() as conn:
        # Цепочки. Проверяем оба конца: и «наш ключ уже чей-то канонический»,
        # и «наш канонический сам куда-то ведёт».
        if conn.execute("SELECT 1 FROM compare_synonym WHERE key_to = ?", (key_from,)).fetchone():
            raise ChainNotAllowed("К этой позиции уже сведена другая — сначала отмените ту пару")
        if conn.execute("SELECT 1 FROM compare_synonym WHERE key_from = ?", (key_to,)).fetchone():
            raise ChainNotAllowed(
                "Позиция, к которой сводим, сама сведена к третьей — выберите конечную"
            )

        existing = conn.execute(
            "SELECT id, key_to FROM compare_synonym WHERE key_from = ?", (key_from,)
        ).fetchone()
        if existing is not None:
            if existing["key_to"] != key_to:
                raise ValueError(
                    "Эта позиция уже сведена к другой. Отмените прежнюю пару, "
                    "если соответствие изменилось"
                )
            return int(existing["id"])

        try:
            cur = conn.execute(
                "INSERT INTO compare_synonym (key_from, key_to, name_from, name_to, confirmed_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (key_from, key_to, name_from.strip(), name_to.strip(), by),
            )
        except sqlite3.IntegrityError as e:  # гонка двух подтверждений подряд
            raise ValueError("Эта позиция уже сведена к другой") from e
        # lastrowid у INSERT в SQLite не бывает None, но проверка типов этого
        # не знает. Молчаливый 0 вернуть нельзя: по этому id пару потом
        # отзывают, и «отменил не ту» здесь дороже лишней строки.
        if cur.lastrowid is None:
            raise RuntimeError("SQLite не вернул id вставленной пары")
        return cur.lastrowid


def forget(pair_id: int) -> bool:
    """Отзывает пару. False — такой записи нет.

    Отзыв обязателен: ошибочная пара иначе живёт вечно и тихо портит каждую
    следующую сверку.
    """
    with connect() as conn:
        cur = conn.execute("DELETE FROM compare_synonym WHERE id = ?", (pair_id,))
        return cur.rowcount > 0
