"""Учётные записи и сессии. Вход — ТОЛЬКО по логину, без пароля.

Приложение работает на сервере и слушает внутреннюю сеть
(docs/07-ops/install-server.md), поэтому понятие «кто пришёл» нужно: по нему
разделяются документы, задачи и история. А вот пароля здесь нет намеренно.

### Что это значит на самом деле

Вход по одному логину — это НЕ защита от постороннего. Логины короткие и
предсказуемые (фамилия сотрудника), и любой, кто дотянулся до сервера и знает
или угадал чужой логин, войдёт под ним. То есть разграничение здесь разделяет
РАБОТУ, а не секреты: чтобы Иванов не путался в документах Петровой и не
стирал её историю.

Решение принято осознанно ради того, чтобы сотрудник ничего не вводил каждый
день: логин запоминается на устройстве и подставляется сам, остаётся нажать
кнопку. Если однажды понадобится настоящая защита от постороннего — это
пароли или доменная аутентификация, и это отдельная работа.

Что из защиты всё-таки осталось:

- **Учётная запись должна существовать.** Заводит её администратор
  (scripts/add_user.py). Придумать логин на ходу и войти нельзя — иначе
  опечатка создавала бы нового «сотрудника», и человек терял бы свою историю.
- **Учётную запись можно отключить** (`disabled`) — единственный способ
  закрыть доступ уволившемуся.
- **Сессии живут в базе**, поэтому выход и отключение записи действуют сразу.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from ..infrastructure.db import connect

log = logging.getLogger(__name__)

# Сколько сессия живёт без обращений. Рабочий день длиннее, поэтому 12 часов
# не выкидывают человека посреди работы, но забытая открытой вкладка на чужом
# компьютере не остаётся входом навсегда.
SESSION_IDLE_HOURS = 12


@dataclass(frozen=True)
class User:
    id: int
    login: str
    is_admin: bool


# --- Пользователи ---


def create_user(login: str, *, is_admin: bool = False) -> int:
    """Заводит учётную запись. Пароля нет — см. модульный docstring."""
    login = login.strip()
    if not login:
        raise ValueError("Пустой логин")
    with connect() as conn:
        cur = conn.execute(
            # Колонки password_hash/salt остались от прежней схемы: SQLite не
            # умеет удалять столбцы без пересоздания таблицы, а пустое значение
            # никому не мешает. Проверка пароля из кода убрана полностью.
            "INSERT INTO users (login, password_hash, salt, is_admin) VALUES (?, ?, ?, ?)",
            (login, b"", b"", int(is_admin)),
        )
        return int(cur.lastrowid)


def set_disabled(login: str, disabled: bool) -> bool:
    """Закрыть или вернуть доступ. Единственный способ отозвать вход у
    уволившегося, раз пароля нет."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE users SET disabled = ? WHERE login = ?", (int(disabled), login.strip())
        )
        changed = cur.rowcount > 0
        if changed and disabled:
            # Иначе отключённый сотрудник продолжает ходить по открытой сессии.
            conn.execute(
                "DELETE FROM sessions WHERE user_id = (SELECT id FROM users WHERE login = ?)",
                (login.strip(),),
            )
    return changed


def list_users() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, login, is_admin, disabled, created_at FROM users ORDER BY login"
        ).fetchall()
    return [dict(r) for r in rows]


def any_users_exist() -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


# --- Вход и сессии ---


def authenticate(login: str) -> User | None:
    """Пользователь по логину или None.

    None — записи нет либо она отключена. Причину наружу не раскрываем: даже
    без пароля не стоит превращать форму входа в справочник существующих
    логинов.
    """
    login = login.strip()
    if not login:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT id, login, is_admin, disabled FROM users WHERE login = ?", (login,)
        ).fetchone()
    if row is None or row["disabled"]:
        log.info("Отказ во входе: %s", login)
        return None
    return User(id=row["id"], login=row["login"], is_admin=bool(row["is_admin"]))


def open_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("INSERT INTO sessions (token, user_id) VALUES (?, ?)", (token, user_id))
    return token


def close_session(token: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def user_for_session(token: str) -> User | None:
    """Пользователь по токену, с продлением сессии. None — токена нет, он
    протух, или учётную запись отключили."""
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT s.token, s.last_seen, u.id, u.login, u.is_admin, u.disabled "
            "FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        ).fetchone()
        if row is None or row["disabled"]:
            return None

        idle_hours = (datetime.now(UTC) - _parse_ts(row["last_seen"])).total_seconds() / 3600
        if idle_hours > SESSION_IDLE_HOURS:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return None

        conn.execute(
            "UPDATE sessions SET last_seen = ? WHERE token = ?",
            (datetime.now(UTC).isoformat(), token),
        )
    return User(id=row["id"], login=row["login"], is_admin=bool(row["is_admin"]))


def _parse_ts(raw: str) -> datetime:
    """Метки времени в базе от двух источников: CURRENT_TIMESTAMP пишет
    «YYYY-MM-DD HH:MM:SS» без зоны, наш код — ISO с зоной."""
    try:
        parsed = datetime.fromisoformat(raw.replace(" ", "T"))
    except ValueError:
        return datetime.now(UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def purge_expired_sessions() -> int:
    cutoff = datetime.now(UTC).timestamp() - SESSION_IDLE_HOURS * 3600
    removed = 0
    with connect() as conn:
        for row in conn.execute("SELECT token, last_seen FROM sessions").fetchall():
            if _parse_ts(row["last_seen"]).timestamp() < cutoff:
                conn.execute("DELETE FROM sessions WHERE token = ?", (row["token"],))
                removed += 1
    return removed
