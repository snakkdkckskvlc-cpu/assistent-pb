"""Завести учётную запись, сменить пароль, посмотреть список.

Пароля по умолчанию в приложении намеренно нет: «admin/admin» на сервере в
общей сети — это открытая дверь, которую забудут закрыть. Поэтому первая
учётная запись заводится руками, здесь.

Запуск:
    python scripts/add_user.py ivanov              # завести, пароль спросит
    python scripts/add_user.py ivanov --admin      # с правами администратора
    python scripts/add_user.py ivanov --set-password
    python scripts/add_user.py --list

Нужен PYTHONPATH=apps/backend/src (как и остальным скриптам).

В выводе только ASCII-маркеры [OK]/[X]/[!]: консоль Windows в cp1251 падает
на эмодзи с UnicodeEncodeError.
"""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "backend" / "src"))

from fire_safety_backend.infrastructure.db import init_db  # noqa: E402
from fire_safety_backend.services import auth  # noqa: E402

_MIN_LEN = 8


def _ask_password() -> str | None:
    """Пароль спрашивается без эха и дважды — опечатку в невидимом вводе
    иначе обнаружит только тот, кто не сможет войти."""
    first = getpass.getpass("Пароль: ")
    if len(first) < _MIN_LEN:
        print(f"[X] Пароль короче {_MIN_LEN} символов")
        return None
    if first != getpass.getpass("Ещё раз: "):
        print("[X] Пароли не совпали")
        return None
    return first


def main() -> int:
    parser = argparse.ArgumentParser(description="Учётные записи Ассистента ПБ")
    parser.add_argument("login", nargs="?", help="логин пользователя")
    parser.add_argument("--admin", action="store_true", help="права администратора")
    parser.add_argument("--set-password", action="store_true", help="сменить пароль существующему")
    parser.add_argument("--list", action="store_true", help="показать список учётных записей")
    args = parser.parse_args()

    init_db()

    if args.list:
        users = auth.list_users()
        if not users:
            print("[!] Учётных записей нет. Заведите: python scripts/add_user.py <логин>")
            return 0
        print(f"Учётных записей: {len(users)}")
        for u in users:
            flags = []
            if u["is_admin"]:
                flags.append("администратор")
            if u["disabled"]:
                flags.append("отключён")
            print(f"  {u['login']:20} {u['created_at']}  {', '.join(flags)}")
        return 0

    if not args.login:
        parser.print_help()
        return 1

    password = _ask_password()
    if password is None:
        return 1

    if args.set_password:
        if not auth.set_password(args.login, password):
            print(f"[X] Нет такого пользователя: {args.login}")
            return 1
        # set_password закрывает все сессии — об этом стоит сказать вслух,
        # иначе смена пароля выглядит как «ничего не произошло».
        print(f"[OK] Пароль изменён: {args.login}. Все его сессии закрыты.")
        return 0

    try:
        auth.create_user(args.login, password, is_admin=args.admin)
    except sqlite3.IntegrityError:
        print(f"[X] Пользователь уже существует: {args.login}")
        print(f"    Сменить пароль: python scripts/add_user.py {args.login} --set-password")
        return 1
    except ValueError as e:
        print(f"[X] {e}")
        return 1

    role = " (администратор)" if args.admin else ""
    print(f"[OK] Учётная запись создана: {args.login}{role}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
