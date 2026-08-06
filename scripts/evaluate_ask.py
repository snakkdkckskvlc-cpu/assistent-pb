#!/usr/bin/env python3
"""Замер функции «вопрос по документу» на размеченном наборе вопросов.

Зачем отдельный скрипт. Функция отвечает свободным текстом, и «работает ли она»
на глаз не определить: три удачных вопроса ничего не говорят о четвёртом.
Проверять руками каждый раз никто не станет, а изменение промпта здесь легко
делает хуже — так уже было дважды за один вечер.

Считаются ТРИ вещи, и вторая важнее первой:

1. Полнота — найдено ли то, что в документе есть (по якорям: подстрокам,
   которые обязаны попасть в ответ или в подтверждённую цитату).
2. Отказ — сказано ли «не найдено» там, где сведений НЕТ. На этом держится всё
   обещание «только из файла»: инструмент, который на пустом месте что-то
   придумывает, бесполезен независимо от полноты.
3. Выдуманные ссылки — цитаты, которых в документе не нашлось. Это прямая
   ложь пользователю, и она страшнее пропуска.

Набор — apps/backend/tests/fixtures/ask/questions.json.

Запуск (из venv проекта):
    python scripts/evaluate_ask.py
    python scripts/evaluate_ask.py --only lyudi
    python scripts/evaluate_ask.py --out отчёт.json

ВНИМАНИЕ по времени: каждый вопрос идёт через ту же модель, что и продукт, на
CPU — считайте по три минуты на вопрос.

В выводе только ASCII-маркеры [OK]/[X]/[!]: консоль Windows в cp1251 падает на
эмодзи с UnicodeEncodeError.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
for _rel in ("apps/backend/src", "packages/rag/src"):
    sys.path.insert(0, str(_REPO_ROOT / _rel))

from _venv import ensure_venv  # noqa: E402

ensure_venv()

FIXTURES = _REPO_ROOT / "apps" / "backend" / "tests" / "fixtures"
QUESTIONS = FIXTURES / "ask" / "questions.json"


def _normalize(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _anchor_hit(anchor: str, result: dict) -> bool:
    """Якорь засчитывается и по ответу, и по ПОДТВЕРЖДЁННОЙ цитате.

    По цитате — потому что ответ модель формулирует своими словами и может
    написать «Ковалёв И. П.» как «Ковалев И.П.»; по ответу — потому что не
    всякое сведение попадает в цитату дословно.
    """
    needle = _normalize(anchor)
    if needle in _normalize(result.get("ответ", "")):
        return True
    return any(
        needle in _normalize(s.get("цитата", ""))
        for s in result.get("источники", [])
        if s.get("проверено")
    )


async def _run_one(spec: dict, text: str) -> dict:
    from fire_safety_backend.pipelines.ask import run_ask

    started = time.time()
    result = await run_ask(spec["вопрос"], text)
    took = time.time() - started

    sources = result.get("источники", [])
    invented = [s for s in sources if not s.get("проверено")]
    missed = [a for a in spec.get("якоря", []) if not _anchor_hit(a, result)]
    found = bool(result.get("найдено"))

    return {
        "id": spec["id"],
        "вопрос": spec["вопрос"],
        "ждали_найдено": spec["найдено"],
        "найдено": found,
        "верный_вердикт": found == spec["найдено"],
        "якорей": len(spec.get("якоря", [])),
        "пропущено_якорей": missed,
        "ссылок": len(sources),
        "выдумано": len(invented),
        "секунд": round(took, 1),
        "ответ": (result.get("ответ") or "")[:400],
        "выдуманные": [str(s.get("цитата", ""))[:120] for s in invented],
    }


def _print_report(rows: list[dict]) -> int:
    total = len(rows)
    verdicts = sum(1 for r in rows if r["верный_вердикт"])
    anchors_total = sum(r["якорей"] for r in rows)
    anchors_missed = sum(len(r["пропущено_якорей"]) for r in rows)
    invented = sum(r["выдумано"] for r in rows)
    seconds = sum(r["секунд"] for r in rows)

    print()
    print("=" * 66)
    print(f"Вопросов: {total} · время {seconds:.0f} c ({seconds / max(1, total):.0f} c на вопрос)")
    print(f"Верный вердикт «найдено / не найдено»: {verdicts} из {total}")
    if anchors_total:
        hit = anchors_total - anchors_missed
        print(f"Якорей найдено: {hit} из {anchors_total} ({100 * hit / anchors_total:.0f}%)")
    print(f"Выдуманных ссылок: {invented}")
    print("=" * 66)

    for r in rows:
        mark = "[OK]" if r["верный_вердикт"] and not r["пропущено_якорей"] else "[X] "
        print(f"\n{mark} {r['id']}: «{r['вопрос']}»  ({r['секунд']} c)")
        if not r["верный_вердикт"]:
            ждали = "найдено" if r["ждали_найдено"] else "НЕ найдено"
            print(f"     ждали «{ждали}», получили «{'найдено' if r['найдено'] else 'не найдено'}»")
        for a in r["пропущено_якорей"]:
            print(f"     [X] не найдено в ответе: «{a}»")
        for q in r["выдуманные"]:
            print(f"     [!] цитата не подтверждена: «{q}»")

    # Выдуманная ссылка — прямая ложь пользователю, поэтому она проваливает
    # замер целиком, даже если полнота высокая.
    return 0 if verdicts == total and invented == 0 and anchors_missed == 0 else 1


async def _main_async(args: argparse.Namespace) -> int:
    spec = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    doc = FIXTURES / "contracts" / spec["документ"]
    if not doc.exists():
        print(f"[X] Документ набора не найден: {doc}")
        return 1
    text = doc.read_text(encoding="utf-8")

    questions = spec["вопросы"]
    if args.only:
        questions = [q for q in questions if q["id"].startswith(args.only)]
    if not questions:
        print(f"[X] По префиксу «{args.only}» вопросов нет")
        return 1

    from fire_safety_backend.infrastructure import llm

    llm.startup()
    try:
        print(f"Документ: {spec['документ']}, {len(text)} символов · вопросов {len(questions)}")
        rows = []
        for i, q in enumerate(questions, start=1):
            print(f"  [{i}/{len(questions)}] {q['id']}…", flush=True)
            rows.append(await _run_one(q, text))
    finally:
        await llm.shutdown()

    code = _print_report(rows)
    if args.out:
        Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nПодробности: {args.out}")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="Замер функции «вопрос по документу»")
    parser.add_argument("--only", help="префикс id вопроса, например lyudi")
    parser.add_argument("--out", help="куда сложить подробный JSON")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
