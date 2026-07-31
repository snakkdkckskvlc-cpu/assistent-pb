#!/usr/bin/env python3
"""Негативные примеры в промпт из отзывов 👎. Запускается РУКАМИ.

Берёт отзывы «👎 с пояснением» за последние 30 дней, вытаскивает из каждого
претензию пользователя и кусок ответа, который его не устроил, и собирает из
них блок «чего делать не надо» в конец промпта.

Почему вручную, а не по расписанию. Промпт — это то, что напрямую определяет
качество разбора договоров. Автоматическая дописка означала бы, что один
раздражённый комментарий («всё не то») молча меняет поведение продукта у всех.
Здесь человек читает, что будет добавлено, и решает.

Куда пишет: resources/prompts/<функция>_negative.txt — отдельный файл, который
pipelines/_prompts.py::load_prompt подклеивает к основному промпту. Файл
ПЕРЕПИСЫВАЕТСЯ целиком на каждом запуске, поэтому запускать можно сколько
угодно раз, а удаление файла возвращает прежнее поведение.

ВАЖНО про цену. Каждый добавленный токен промпта отнимается от текста
договора: бюджет одной части считается как «окно минус ответ минус промпт
минус нормы» (pipelines/legal.py::_input_budget_tokens). Скрипт показывает,
во что обойдётся добавка, и отказывается превышать потолок.

Запуск (из venv проекта):
    python scripts/update_prompts_from_feedback.py --dry-run    # только показать
    python scripts/update_prompts_from_feedback.py              # записать
    python scripts/update_prompts_from_feedback.py --days 90
    python scripts/update_prompts_from_feedback.py --clear      # убрать примеры
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Скрипт мог быть запущен системным python — тогда зависимостей приложения
# в нём нет, и первый же импорт упал бы с невнятным ModuleNotFoundError.
# Перезапускаемся интерпретатором venv.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _venv import ensure_venv  # noqa: E402

ensure_venv()


_ROOT = Path(__file__).resolve().parent.parent

# Потолок на весь блок негативных примеров. 1200 символов ≈ 600 токенов ≈ 130
# слов договора из бюджета одной части — заметно, но терпимо. Больше — и на
# слабой машине частей станет ощутимо больше, а значит и время анализа.
_MAX_BLOCK_CHARS = 1200
# Сколько от «плохого» ответа показывать в примере. Целиком его класть нельзя:
# один разбор договора — это десятки килобайт.
_MAX_EXCERPT_CHARS = 220
_HEADER = "ЧЕГО ДЕЛАТЬ НЕ НАДО (по замечаниям пользователей на реальных разборах):"


def _excerpt(bad_output: str) -> str:
    """Кусок ответа модели, пригодный для показа в промпте."""
    text = " ".join(str(bad_output or "").split())
    if not text:
        return ""
    return text[:_MAX_EXCERPT_CHARS] + ("…" if len(text) > _MAX_EXCERPT_CHARS else "")


def _build_block(items: list[dict]) -> tuple[str, list[dict]]:
    """Собирает текст блока и возвращает его вместе с реально вошедшими отзывами."""
    lines = [_HEADER]
    used: list[dict] = []
    for item in items:
        comment = " ".join(str(item.get("comment", "")).split())
        if not comment:
            continue
        excerpt = _excerpt(item.get("bad_output", ""))
        entry = f"- Не делай так: {comment}"
        if excerpt:
            entry += f'\n  Пример неудачного ответа: "{excerpt}"'
        candidate = "\n".join([*lines, entry])
        if len(candidate) > _MAX_BLOCK_CHARS:
            break
        lines.append(entry)
        used.append(item)
    return ("\n".join(lines) if used else ""), used


def _budget_impact(function: str, block: str) -> str:
    """Во что добавка обойдётся бюджету части договора."""
    if function != "legal" or not block:
        return ""
    try:
        from fire_safety_backend.pipelines import legal as legal_module
        from fire_safety_backend.pipelines._prompts import load_prompt
    except Exception:  # noqa: BLE001 — импорт бэкенда не обязателен для отчёта
        return ""
    base = load_prompt("legal")
    before = legal_module._contract_part_word_budget(base)
    after = legal_module._contract_part_word_budget(f"{base}\n\n{block}\n")
    return f"бюджет части договора: {before} → {after} слов ({after - before:+d})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Негативные примеры в промпт из отзывов 👎")
    parser.add_argument("--days", type=int, default=30, help="За сколько дней брать отзывы")
    parser.add_argument("--function", default="legal", help="Какой промпт обновлять")
    parser.add_argument("--dry-run", action="store_true", help="Показать, но не записывать")
    parser.add_argument("--clear", action="store_true", help="Удалить негативные примеры")
    args = parser.parse_args()

    sys.path.insert(0, str(_ROOT / "apps" / "backend" / "src"))
    sys.path.insert(0, str(_ROOT / "packages" / "rag" / "src"))

    from fire_safety_backend.infrastructure.db import init_db
    from fire_safety_backend.pipelines._prompts import negative_prompt_path
    from fire_safety_backend.services import feedback as feedback_service

    # В приложении схему поднимает lifespan; отдельный скрипт обязан сделать
    # это сам, иначе на рабочей базе со старой схемой запрос упадёт на
    # отсутствующем столбце bad_output (init_db идемпотентен и применяет
    # миграции).
    init_db()

    target = negative_prompt_path(args.function)

    if args.clear:
        if target.exists():
            target.unlink()
            print(f"Удалено: {target}")
        else:
            print("Негативных примеров и не было.")
        return 0

    items = feedback_service.list_negative(days=args.days, function=args.function)
    if not items:
        print(f"За {args.days} дн. нет отзывов 👎 с пояснением по функции «{args.function}».")
        print("Без пояснения отзыв использовать нельзя: «плохо» не превратить в правило.")
        return 0

    block, used = _build_block(items)
    if not block:
        print("Из отзывов не удалось собрать ни одного правила.")
        return 0

    print(f"Отзывов подходит: {len(items)}, войдёт в промпт: {len(used)}")
    if len(used) < len(items):
        print(f"Остальные не поместились в потолок {_MAX_BLOCK_CHARS} символов — они не потеряны,")
        print("останутся в базе и попадут в следующий раз, когда старые уйдут за окно по дате.")
    impact = _budget_impact(args.function, block)
    if impact:
        print(impact)
    print("\n--- будет добавлено в конец промпта ---")
    print(block)
    print("--- конец ---\n")

    if args.dry_run:
        print("Это --dry-run, файл не тронут.")
        return 0

    target.write_text(block + "\n", encoding="utf-8")
    print(f"Записано: {target}")
    print("Проверьте текст глазами и закоммитьте — это изменение поведения продукта.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
