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

Полноты мало. Размеченный набор писали мы, и цифру полноты легко поднять,
разрешив модели «находить» больше. Поэтому есть второй режим — прогон по
ПРОИЗВОЛЬНОМУ вычитанному документу, где ошибок быть не должно и каждая
находка подозрительна:

    python scripts/evaluate_spellcheck.py --noise apps/backend/tests/fixtures/contracts/03_ognezashchita.txt

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
import re
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


def _span(fragment: str, text: str) -> tuple[int, int] | None:
    """Где фрагмент стоит в документе. None — не нашли.

    Ищется дословно, затем по последовательности слов со свободными знаками
    между ними: цитата модели может отличаться от документа пробелами.
    """
    if not fragment.strip():
        return None
    pos = text.find(fragment)
    if pos >= 0:
        return pos, pos + len(fragment)
    words = re.findall(r"\w+", fragment, flags=re.UNICODE)
    if len(words) < 2:
        return None
    pattern = r"[^\w]*".join(re.escape(w) for w in words)
    m = re.search(pattern, text, flags=re.IGNORECASE | re.UNICODE)
    return (m.start(), m.end()) if m else None


def _matches(before: str, anchor: str, text: str = "") -> bool:
    """Попала ли правка В ТО ЖЕ МЕСТО документа, что ожидаемая ошибка.

    ### Почему по отрезкам, а не по вложенности строк

    Раньше здесь стояло «одна строка содержится в другой». Этого мало: правка
    может ПЕРЕКРЫВАТЬ ожидаемое место, выходя за его границы, — «компания
    надёжный партнёр и дорожит» против якоря «Наша компания надёжный партнёр».
    Ни одна строка не содержит другую, и верная правка попадала одновременно в
    пропущенные И в лишние: полнота занижалась, шум завышался.

    Дефект нашли при внешнем ревью, и он обесценивал сравнения: вчера на таких
    цифрах принимались решения откатить промпт и откатить упаковку документа.

    Теперь оба фрагмента ищутся в исходном тексте, и правка засчитывается, если
    отрезки пересекаются. Вложенность строк осталась запасным путём — на случай,
    когда фрагмент в тексте не находится вовсе.
    """
    b, a = _normalize(before), _normalize(anchor)
    if len(b) < _MIN_OVERLAP or len(a) < _MIN_OVERLAP:
        return False
    if text:
        bs, as_ = _span(before, text), _span(anchor, text)
        if bs and as_:
            return bs[0] < as_[1] and as_[0] < bs[1]
    return b in a or a in b


def _score(
    findings: list[dict], expected: list[dict], text: str = ""
) -> tuple[list[dict], list[dict], list[dict]]:
    """Возвращает (найденные, пропущенные, лишние).

    Каждая находка засчитывается не больше одного раза, иначе одна удачная
    правка закрывала бы сразу несколько ожидаемых ошибок и завышала бы recall.
    """
    unused = list(findings)
    found: list[dict] = []
    missed: list[dict] = []

    for exp in expected:
        hit = next((f for f in unused if _matches(f.get("before", ""), exp["anchor"], text)), None)
        if hit is None:
            missed.append(exp)
        else:
            unused.remove(hit)
            found.append({**exp, "нашли": hit.get("before", ""), "источник": hit.get("source", "")})
    return found, missed, unused


def _in_clean_part(before: str, clean: list[str]) -> bool:
    b = _normalize(before)
    return any(b in _normalize(fragment) for fragment in clean) and len(b) >= _MIN_OVERLAP


async def _wait_for_languagetool(languagetool, timeout_sec: int = 90) -> bool:
    """Ждёт готовности LanguageTool и говорит, дождался ли.

    Приложение ждать НЕ должно: java поднимается порядка 15 секунд, а старт
    доводили до 3.2 с, и проверка орфографии при неготовом сервере штатно
    деградирует на модель. Для замера это ровно наоборот: молча потерять весь
    детерминированный проход значит померить не то приложение и не заметить
    этого. Поймано на живом прогоне — первый же документ считался без словаря.
    """
    deadline = time.time() + timeout_sec
    announced = False
    while time.time() < deadline:
        if (await languagetool.healthcheck()).get("ok"):
            return True
        if not announced:
            print("Жду LanguageTool (java поднимается ~15 с)...")
            announced = True
        await asyncio.sleep(2)
    return False


async def _run_one(path: Path, deep: bool) -> dict:
    from fire_safety_backend.pipelines.spellcheck import run_spellcheck

    expected_path = path.with_suffix(".expected.json")
    spec = json.loads(expected_path.read_text(encoding="utf-8"))
    text = path.read_text(encoding="utf-8")

    started = time.time()
    result = await run_spellcheck(text, deep=deep)
    took = time.time() - started

    findings = [e for e in result.get("errors", []) if isinstance(e, dict)]
    found, missed, extra = _score(findings, spec["errors"], text)
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

    false_positives = sum(len(r["false_positives"]) for r in reports)
    extra = sum(len(r["extra"]) for r in reports)

    print()
    print("=" * 62)
    print(f"Режим: {'полный (LanguageTool + модель)' if deep else 'быстрый (только LanguageTool)'}")
    print(f"Найдено: {hit} из {total}  ({100 * hit / total:.0f}%)   время: {seconds:.1f} c")
    # Шум — во второй строке сводки, а не только в подробностях. Полнота без
    # него накручивается тривиально: разреши модели «находить» больше, и
    # процент вырастет вместе с числом мусорных правок.
    print(f"Шум: {false_positives} ложных в чистых фрагментах, {extra} находок сверх набора")
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


async def _noise_check(path: Path, deep: bool, limit: int) -> int:
    """Сколько находок пайплайн даёт на ПРОИЗВОЛЬНОМ документе.

    Размеченный набор меряет полноту — сколько подсаженных ошибок нашли. Он
    ничего не говорит о том, что инструмент скажет о ЧУЖОМ вычитанном тексте, а
    там ошибок почти нет, и каждая находка подозрительна. Замерено на настоящем
    договоре: модель выдавала около десятка правок на 2000 символов правильного
    юридического текста, включая порчу номера пункта.

    Полнота без этой проверки обманчива: любую цифру полноты можно поднять,
    разрешив модели «находить» больше.
    """
    from fire_safety_backend.pipelines.spellcheck import run_spellcheck

    text = path.read_text(encoding="utf-8", errors="replace")[:limit]
    print(f"Документ: {path.name}, взято {len(text)} символов")
    started = time.time()
    result = await run_spellcheck(text, deep=deep)
    errors = [e for e in result.get("errors", []) if isinstance(e, dict)]
    by_source: dict[str, int] = {}
    for e in errors:
        by_source[e.get("source", "?")] = by_source.get(e.get("source", "?"), 0) + 1
    print(f"Находок: {len(errors)} за {time.time() - started:.0f} c · по источникам {by_source}\n")
    for e in errors:
        print(f"  [{e.get('source', '?')}] «{e.get('before', '')[:90]}»")
        print(f"      -> «{e.get('after', '')[:90]}»")
    print("\nКаждую находку надо посмотреть глазами: текст считается правильным,")
    print("значит всё найденное — либо настоящая ошибка источника, либо ложняк.")
    return 0


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
        if not await _wait_for_languagetool(languagetool):
            print("[X] LanguageTool не поднялся за отведённое время.")
            print("    Замер без него бессмысленен: пропали бы ВСЕ находки словаря,")
            print("    и цифра вышла бы красивой, но не про то приложение.")
            print("    Поднять вручную: .\\tools\\languagetool\\start.ps1")
            return 1
        if args.noise:
            return await _noise_check(Path(args.noise), deep=not args.fast, limit=args.limit)
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
    parser.add_argument(
        "--noise",
        metavar="ФАЙЛ",
        help="прогнать по ПРОИЗВОЛЬНОМУ документу и показать все находки: "
        "на вычитанном чужом тексте каждая из них подозрительна",
    )
    parser.add_argument(
        "--limit", type=int, default=2000, help="сколько символов брать в режиме --noise"
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
