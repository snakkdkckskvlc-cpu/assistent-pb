"""Запуск в серверном режиме: слушать сеть, а не только себя.

Отличие от десктопного запуска (`fire_safety_desktop.main`): нет окна
pywebview, backend слушает 0.0.0.0, и сотрудники открывают его браузером с
любого компьютера. Десктопную обёртку на сервере запускать не надо — она
поднимает окно, которого там некому смотреть.

Запуск:
    python scripts/run_server.py                 # 0.0.0.0:8000
    python scripts/run_server.py --port 8080
    python scripts/run_server.py --host 127.0.0.1  # только локально, для проверки

Нужен PYTHONPATH=apps/backend/src;packages/rag/src.

### Почему ровно один рабочий процесс

Очередь задач и их результаты живут в памяти процесса
(infrastructure/queue.py). При двух процессах запрос `GET /api/tasks/{id}`
попадёт в другой процесс и вернёт 404 — задача считается в первом. Ollama всё
равно выполняет запросы к одной модели последовательно (замерено), так что
вторым процессом производительности не добавить.

### Что защищает сервер, доступный всей сети

Только вход по паролю (services/auth.py). Учётные записи заводятся
scripts/add_user.py; пароля по умолчанию нет намеренно. Перед первым запуском
на сервере убедитесь, что хотя бы одна запись создана — иначе войти будет
некому.

В выводе только ASCII-маркеры: консоль Windows в cp1251 падает на эмодзи.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for rel in ("apps/backend/src", "packages/rag/src"):
    sys.path.insert(0, str(_REPO_ROOT / rel))

DEFAULT_HOST = "0.0.0.0"  # noqa: S104 — в этом и смысл серверного режима
DEFAULT_PORT = 8000


def main() -> int:
    parser = argparse.ArgumentParser(description="Ассистент ПБ — серверный режим")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    import uvicorn
    from fire_safety_backend.services import auth

    from fire_safety_backend.infrastructure.db import init_db  # isort: skip

    init_db()
    if not auth.any_users_exist():
        # Не отказ: сервер поднимется, но войти будет некому, и об этом надо
        # сказать до, а не после того как сотрудники начнут звонить.
        print("[!] Учётных записей нет — войти никто не сможет.")
        print("    Создайте: python scripts/add_user.py <логин> --admin")

    print(f"[OK] Слушаю http://{args.host}:{args.port}")
    if args.host == DEFAULT_HOST:
        print("     Доступен из внутренней сети. Наружу не публиковать.")

    # workers=1 — не настройка производительности, а требование: см. docstring.
    uvicorn.run(
        "fire_safety_backend.main:app",
        host=args.host,
        port=args.port,
        workers=1,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
