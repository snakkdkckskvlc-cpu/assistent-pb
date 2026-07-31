"""Завести учётную запись, отключить, посмотреть список.

Пароля нет: вход в приложение идёт только по логину, и логин запоминается на
устройстве (см. services/auth.py — там же честная оговорка, чего это стоит).
Придумать логин на ходу и войти нельзя: учётную запись заводит администратор,
здесь. Иначе опечатка в логине создавала бы нового «сотрудника», и человек
терял бы доступ к своим документам.

Запуск:
    python scripts/add_user.py ivanov              # завести
    python scripts/add_user.py ivanov --admin      # с правами администратора
    python scripts/add_user.py ivanov --disable    # закрыть доступ уволившемуся
    python scripts/add_user.py ivanov --enable
    python scripts/add_user.py --list

Нужен PYTHONPATH=apps/backend/src (как и остальным скриптам).

В выводе только ASCII-маркеры [OK]/[X]/[!]: консоль Windows в cp1251 падает
на эмодзи с UnicodeEncodeError.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "apps" / "backend" / "src"))

from fire_safety_backend.infrastructure.db import init_db  # noqa: E402
from fire_safety_backend.services import auth  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Учётные записи Ассистента ПБ")
    parser.add_argument("login", nargs="?", help="логин пользователя")
    parser.add_argument("--admin", action="store_true", help="права администратора")
    parser.add_argument("--disable", action="store_true", help="закрыть доступ")
    parser.add_argument("--enable", action="store_true", help="вернуть доступ")
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

    if args.disable or args.enable:
        if not auth.set_disabled(args.login, args.disable):
            print(f"[X] Нет такого пользователя: {args.login}")
            return 1
        if args.disable:
            # Сессии закрываются вместе с доступом — об этом стоит сказать
            # вслух, иначе «отключил, а он всё ещё работает» выглядит багом.
            print(f"[OK] Доступ закрыт: {args.login}. Его сессии завершены.")
        else:
            print(f"[OK] Доступ возвращён: {args.login}")
        return 0

    try:
        auth.create_user(args.login, is_admin=args.admin)
    except sqlite3.IntegrityError:
        print(f"[X] Пользователь уже существует: {args.login}")
        return 1
    except ValueError as e:
        print(f"[X] {e}")
        return 1

    role = " (администратор)" if args.admin else ""
    print(f"[OK] Учётная запись создана: {args.login}{role}")
    print("     Пароля нет — вход по логину, он запомнится на компьютере сотрудника.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
