"""Скачать модель эмбеддингов заранее, до того как приложению запретят сеть.

Зачем это отдельный шаг. Приложению запрещён выход в интернет
(apps/backend/.../infrastructure/netguard.py), а модель `intfloat/multilingual-e5-large`
(~1.3 ГБ) раньше скачивалась лениво — при первом обращении к нормативной базе.
С запретом она не скачается никогда, и RAG молча уйдёт в no-op: «нормативная
база не подключена» без объяснения причины.

Скрипт запускается установщиком (bootstrap.ps1) и вручную. Сеть ему доступна:
netguard включается на импорте fire_safety_backend.main, а этот скрипт его не
импортирует.

Запуск:
    python scripts/warm_models.py
    python scripts/warm_models.py --check    # только проверить, не качать

Запускать можно любым python: скрипт сам найдёт venv проекта.

В выводе только ASCII-маркеры [OK]/[X]/[!]: консоль Windows в cp1251 падает
на эмодзи с UnicodeEncodeError.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "rag" / "src"))

# Скрипт мог быть запущен системным python — тогда зависимостей приложения
# в нём нет, и импорт ниже упал бы с невнятным ModuleNotFoundError.
# Перезапускаемся интерпретатором venv.
from _venv import ensure_venv  # noqa: E402

ensure_venv()

from fire_safety_rag import config, embed_model_cached  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Предзагрузка модели эмбеддингов")
    parser.add_argument(
        "--check",
        action="store_true",
        help="только проверить наличие в кеше, ничего не качать",
    )
    args = parser.parse_args()

    print(f"Модель: {config.EMBED_MODEL}")

    if embed_model_cached():
        print("[OK] Уже в кеше HuggingFace — качать нечего.")
        return 0

    if args.check:
        print("[X] В кеше нет. Запустите без --check, пока есть интернет.")
        return 1

    print("[!] В кеше нет. Скачиваю (~1.3 ГБ, это единственный раз)...")
    try:
        # Импорт внутри функции: sentence_transformers тянет torch, это
        # несколько секунд, и при --check они не нужны.
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        print(f"[X] sentence-transformers не установлен: {e}")
        return 1

    try:
        SentenceTransformer(config.EMBED_MODEL)
    except Exception as e:
        # Чаще всего это отсутствие интернета. Сообщение должно вести к
        # действию, а не к трассировке.
        print(f"[X] Не удалось скачать: {e}")
        print("    Нужен доступ в интернет. Если приложение уже работает")
        print("    офлайн, скопируйте кеш HuggingFace с машины, где модель есть.")
        return 1

    if not embed_model_cached():
        # Модель загрузилась, но в ожидаемом месте кеша её нет — значит
        # проверка наличия и реальный кеш разошлись, и предупреждение в
        # интерфейсе будет врать.
        print("[X] Модель скачалась, но в кеше не найдена — проверьте HF_HOME/HF_HUB_CACHE.")
        return 1

    print("[OK] Модель скачана и лежит в кеше.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
