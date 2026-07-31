"""Учётные записи и сессии.

Приложение переезжает с рабочих мест на сервер и слушает всю внутреннюю сеть
(см. docs/07-ops/install-server.md). До этого разграничения доступа не было
вовсе — и было безопасно ровно пока backend слушал 127.0.0.1. Теперь любой в
сети дотягивается до договоров компании, и единственная преграда — этот модуль.

### Почему так, а не иначе

- **Хеш — `hashlib.scrypt` из стандартной библиотеки.** Память-жёсткий, то
  есть перебор по украденной базе дорог не только по процессору. Новых
  зависимостей не появляется: установщик этого проекта и так хрупкий, и тянуть
  ради хеширования bcrypt/argon2 незачем.
- **Сессии в базе, а не подписанные cookie.** Подписанный токен живёт до
  истечения срока, и отозвать его нечем: уволенный сотрудник ходил бы до
  конца срока. Запись в БД удаляется выходом или отключением учётной записи.
- **Пароля по умолчанию нет.** На сервере в общей сети «admin/admin», который
  забудут сменить, — это открытая дверь. Пока учётных записей нет, приложение
  не пускает никого и говорит, чем их завести (scripts/add_user.py).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime

from ..infrastructure.db import connect

log = logging.getLogger(__name__)

# Параметры scrypt. n=2**14 при r=8 — около 16 МБ памяти на проверку: заметно
# для перебора и незаметно для одного входа раз в день.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_KEY_LEN = 32
_SALT_LEN = 16

# Сколько сессия живёт без обращений. Рабочий день длиннее, поэтому 12 часов
# не выкидывают человека посреди работы, но забытая открытой вкладка на чужом
# компьютере не остаётся входом навсегда.
SESSION_IDLE_HOURS = 12

# Задержка после неудачных попыток — от перебора пароля. Счётчик в памяти
# процесса: сервер один (uvicorn с одним воркером), внешнего хранилища ради
# этого заводить незачем.
_MAX_ATTEMPTS_BEFORE_DELAY = 3
_DELAY_SEC = 2.0
_failed_attempts: dict[str, int] = {}


@dataclass(frozen=True)
class User:
    id: int
    login: str
    is_admin: bool


def hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    """(хеш, соль). Соль своя у каждого пароля — одинаковые пароли разных
    людей дают разные хеши, и радужная таблица бесполезна."""
    salt = salt or secrets.token_bytes(_SALT_LEN)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_KEY_LEN,
    )
    return digest, salt


def verify_password(password: str, expected: bytes, salt: bytes) -> bool:
    digest, _ = hash_password(password, salt)
    # compare_digest, а не ==: обычное сравнение выходит на первом различии, и
    # по времени ответа можно подбирать хеш побайтово.
    return secrets.compare_digest(digest, expected)


# --- Пользователи ---


def create_user(login: str, password: str, *, is_admin: bool = False) -> int:
    login = login.strip()
    if not login:
        raise ValueError("Пустой логин")
    if len(password) < 8:
        raise ValueError("Пароль короче 8 символов")
    digest, salt = hash_password(password)
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (login, password_hash, salt, is_admin) VALUES (?, ?, ?, ?)",
            (login, digest, salt, int(is_admin)),
        )
        return int(cur.lastrowid)


def set_password(login: str, password: str) -> bool:
    if len(password) < 8:
        raise ValueError("Пароль короче 8 символов")
    digest, salt = hash_password(password)
    with connect() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, salt = ? WHERE login = ?",
            (digest, salt, login.strip()),
        )
        changed = cur.rowcount > 0
        if changed:
            # Смена пароля обязана разлогинивать: иначе тот, из-за кого пароль
            # меняли, продолжает ходить по своей старой сессии.
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


def authenticate(login: str, password: str) -> User | None:
    """Пользователь или None. Причину неудачи наружу НЕ раскрываем: «нет
    такого логина» и «неверный пароль» вместе — это подсказка перебирающему,
    какие логины существуют."""
    login = login.strip()
    if _failed_attempts.get(login, 0) >= _MAX_ATTEMPTS_BEFORE_DELAY:
        time.sleep(_DELAY_SEC)

    with connect() as conn:
        row = conn.execute(
            "SELECT id, login, password_hash, salt, is_admin, disabled FROM users WHERE login = ?",
            (login,),
        ).fetchone()

    if row is None or row["disabled"]:
        _failed_attempts[login] = _failed_attempts.get(login, 0) + 1
        return None
    if not verify_password(password, row["password_hash"], row["salt"]):
        _failed_attempts[login] = _failed_attempts.get(login, 0) + 1
        log.warning("Неудачный вход: %s", login)
        return None

    _failed_attempts.pop(login, None)
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


def reset_failed_attempts() -> None:
    """Нужно тестам: счётчик живёт в памяти процесса и протекал бы между ними."""
    _failed_attempts.clear()
