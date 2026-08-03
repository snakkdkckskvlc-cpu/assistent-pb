#!/usr/bin/env python3
"""Замер качества проверки орфографии и пунктуации на размеченном наборе.

Зачем отдельный скрипт. Цифры вроде «16 из 19» до сих пор жили в комментариях к
коду: набор, на котором их получили, нигде не хранился. Значит их нельзя было ни
повторить, ни сравнить с новой версией промпта — а регрессия («стало 11 из 19»)
прошла бы незамеченной, потому что тихая деградация здесь выглядит как обычная
работа.

Набор — `apps/backend/tests/fixtures/spellcheck/`: письмо `NN_имя.txt` плюс
разметка `NN_имя.expected.json`. Каждая ошибка привязана к ТОЧНОЙ подстроке
(`anchor`), поэтому засчитывается попадание в нужное место текста, а не
совпадение формулировок: «пропущена запятая» и «нужна запятая после обращения»
одинаково верны.

Запуск (из venv проекта):
    python scripts/evaluate_spellcheck.py            # полный проход (LT + модель)
    python scripts/evaluate_spellcheck.py --fast     # только LanguageTool
    python scripts/evaluate_spellcheck.py --only 01

ВНИМАНИЕ по времени: полный проход идёт через ту же модель, что и продукт, на
CPU — это минуты. Быстрый режим отвечает за секунды.

Нужны поднятые Ollama и LanguageTool — то же, что нужно самому приложению.

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

FIXTURES = _REPO_ROOT / "apps" / "backend" / "tests" / "fixtures" / "spellcheck"

# Ниже этой длины совпадение подстрок ничего не значит: «и» или «за» найдутся
# внутри любого анchor и засчитали бы промах как попадание.
_MIN_OVERLAP = 4


def _normalize(text: str) -> str:
    """Та же нормализация, что в pipelines/spellcheck.py::_normalize_before —
    сравнение обязано вести себя одинаково в замере и в самом приложении."""
    return " ".join(str(text).split()).casefold()


def _matches(before: str, anchor: str) -> bool:
    b, a = _normalize(before), _normalize(anchor)
    if len(b) < _MIN_OVERLAP or len(a) < _MIN_OVERLAP:
        return False
    return b in a or a in b


def _score(findings: list[dict], expected: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Возвращает (найденные, пропущенные, лишние).

    Каждая находка засчитывается не больше одного раза, иначе одна удачная
    правка закрывала бы сразу несколько ожидаемых ошибок и завышала бы recall.
    """
    unused = list(findings)
    found: list[dict] = []
    missed: list[dict] = []

    for exp in expected:
        hit = next((f for f in unused if _matches(f.get("before", ""), exp["anchor"])), None)
        if hit is None:
            missed.append(exp)
        else:
            unused.remove(hit)
            found.append({**exp, "нашли": hit.get("before", ""), "источник": hit.get("source", "")})
    return found, missed, unused


def _in_clean_part(before: str, clean: list[str]) -> bool:
    b = _normalize(before)
    return any(b in _normalize(fragment) for fragment in clean) and len(b) >= _MIN_OVERLAP


async def _run_one(path: Path, deep: bool) -> dict:
    from fire_safety_backend.pipelines.spellcheck import run_spellcheck

    expected_path = path.with_suffix(".expected.json")
    spec = json.loads(expected_path.read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")

    started = time.time()
    result = await run_spellcheck(text, deep=deep)
    took = time.time() - started

    findings = [e for e in result.get("errors", []) if isinstance(e, dict)]
    found, missed, extra = _score(findings, spec["errors"])
    clean = spec.get("чистые_фрагменты", [])
    false_positives = [f for f in extra if _in_clean_part(f.get("before", ""), clean)]

    return {
        "документ": path.name,
        "всего_ошибок": len(spec["errors"]),
        "найдено": len(found),
        "секунд": round(took, 1),
        "found": found,
        "missed": missed,
        "extra": extra,
        "false_positives": false_positives,
    }


def _print_report(reports: list[dict], deep: bool) -> None:
    total = sum(r["всего_ошибок"] for r in reports)
    hit = sum(r["найдено"] for r in reports)
    seconds = sum(r["секунд"] for r in reports)

    print()
    print("=" * 62)
    print(f"Режим: {'полный (LanguageTool + модель)' if deep else 'быстрый (только LanguageTool)'}")
    print(f"Найдено: {hit} из {total}  ({100 * hit / total:.0f}%)   время: {seconds:.1f} c")
    print("=" * 62)

    for r in reports:
        print(f"\n{r['документ']}: {r['найдено']} из {r['всего_ошибок']}  ({r['секунд']} c)")
        for m in r["missed"]:
            print(f"  [X] ПРОПУЩЕНО [{m['kind']}] «{m['anchor']}»")
        for f in r["false_positives"]:
            print(f"  [!] ЛОЖНОЕ в чистом фрагменте: «{f.get('before', '')}»")
        other_extra = [e for e in r["extra"] if e not in r["false_positives"]]
        for e in other_extra:
            print(f"  [ ] сверх набора: «{e.get('before', '')}» -> «{e.get('after', '')}»")

    print()
    by_kind: dict[str, list[bool]] = {}
    for r in reports:
        for f in r["found"]:
            by_kind.setdefault(f["kind"], []).append(True)
        for m in r["missed"]:
            by_kind.setdefault(m["kind"], []).append(False)
    print("По категориям:")
    for kind, results in sorted(by_kind.items()):
        good = sum(results)
        mark = "[OK]" if good == len(results) else "[X] "
        print(f"  {mark} {kind}: {good}/{len(results)}")


async def _main_async(args: argparse.Namespace) -> int:
    paths = sorted(p for p in FIXTURES.glob("*.txt"))
    if args.only:
        paths = [p for p in paths if p.name.startswith(args.only)]
    if not paths:
        print(f"[X] Не найдено ни одного письма в {FIXTURES}")
        return 1

    # Те же startup/shutdown, что делает lifespan приложения: клиенты к
    # LanguageTool и Ollama живут на всё приложение, а не создаются на вызов.
    # Без этого check() честно падает с «клиент не запущен».
    from fire_safety_backend.infrastructure import languagetool, llm

    languagetool.startup()
    llm.startup()
    try:
        reports = [await _run_one(p, deep=not args.fast) for p in paths]
    finally:
        await llm.shutdown()
        await languagetool.shutdown()
    _print_report(reports, deep=not args.fast)

    if args.out:
        Path(args.out).write_text(
            json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nПодробности: {args.out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Замер проверки орфографии и пунктуации")
    parser.add_argument("--fast", action="store_true", help="только LanguageTool, без модели")
    parser.add_argument("--only", help="префикс имени файла, например 01")
    parser.add_argument("--out", help="куда сложить подробный JSON")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
