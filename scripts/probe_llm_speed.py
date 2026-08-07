#!/usr/bin/env python3
"""Куда уходит время одного запроса к модели и работает ли кэш префикса.

Зачем. Юр. анализ договора идёт 40 минут, а по расчёту должен около 18:
чтение промпта 18–20 тыс. токенов при 165–260 т/с даёт 70–120 с, генерация
~12 000 токенов при 12 т/с — около 1000 с. Недостающие двадцать минут нужно
увидеть, а не угадать: оптимизировать сорокаминутную задачу вслепую
бессмысленно. Ollama отдаёт разбивку по фазам в финальном чанке ответа —
скрипт её печатает и складывает.

Вторая проверка — кэш префикса. В ollama#14780 («KV cache completely
non-functional on CPU backend», открыт 11.03.2026) утверждается, что на
CPU-бэкенде кэш не работает вовсе. Проверяется одним и тем же запросом,
посланным дважды подряд, и судить приходится ТОЛЬКО по времени: при попадании
в кэш `prompt_eval_count` всё равно показывает полный размер промпта.
Подробнее, включая две ошибки, на которые эта проба напрашивается, — в
докстринге _probe_cache.

Самостоятельный скрипт (только httpx), запускается на боевой машине:

    python scripts/probe_llm_speed.py
    python scripts/probe_llm_speed.py --model qwen2.5:7b-instruct --num-ctx 32768

Ничего не меняет и никуда не пишет — только читает и печатает.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Скрипт не импортирует пакеты приложения — ему хватает httpx. Но httpx стоит
# в venv проекта, а не в системном Python, и запуск командой из документации
# («python scripts/probe_llm_speed.py») у системного интерпретатора падал бы с
# ModuleNotFoundError, указывающим на httpx вместо настоящей причины.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _venv import ensure_venv  # noqa: E402

ensure_venv()

import httpx  # noqa: E402

DEFAULT_HOST = "http://127.0.0.1:11434"
_ROOT = Path(__file__).resolve().parent.parent
_LEGAL_PROMPT = (
    _ROOT
    / "apps"
    / "backend"
    / "src"
    / "fire_safety_backend"
    / "resources"
    / "prompts"
    / "legal.txt"
)
_CONTRACT = _ROOT / "apps" / "backend" / "tests" / "fixtures" / "contracts" / "01_montazh_aps.txt"


def _sec(stats: dict, key: str) -> float:
    """Длительности Ollama приходят в наносекундах."""
    value = stats.get(key)
    return value / 1e9 if isinstance(value, int | float) else 0.0


def _ask(host: str, client: httpx.Client, model: str, system: str, user: str, opts: dict) -> dict:
    """Один запрос. Возвращает статистику Ollama целиком."""
    r = client.post(
        f"{host}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "options": opts,
            "keep_alive": -1,
        },
    )
    r.raise_for_status()
    return r.json()


def _report(title: str, stats: dict) -> dict:
    read = _sec(stats, "prompt_eval_duration")
    write = _sec(stats, "eval_duration")
    load = _sec(stats, "load_duration")
    total = _sec(stats, "total_duration")
    in_tokens = stats.get("prompt_eval_count") or 0
    out_tokens = stats.get("eval_count") or 0
    other = max(0.0, total - load - read - write)

    print(f"\n{title}")
    print(f"  загрузка модели   {load:8.1f} с")
    print(
        f"  чтение промпта    {read:8.1f} с   {in_tokens:6} ток   "
        f"{in_tokens / read if read else 0:7.1f} ток/с"
    )
    print(
        f"  генерация         {write:8.1f} с   {out_tokens:6} ток   "
        f"{out_tokens / write if write else 0:7.1f} ток/с"
    )
    print(
        f"  прочее            {other:8.1f} с   ({other / total * 100 if total else 0:.0f}% от всего)"
    )
    print(f"  ВСЕГО             {total:8.1f} с")
    return {"in": in_tokens, "read": read, "total": total}


def _probe_cache(host: str, client: httpx.Client, model: str, system: str, opts: dict) -> None:
    """Работает ли кэш префикса.

    Судить можно ТОЛЬКО по времени. `prompt_eval_count` при попадании в кэш
    всё равно показывает полный размер промпта — проверено здесь же: запрос,
    прочитавший 2024 токена за 1.0 с (2100 ток/с при физическом потолке
    машины около 40), отчитался о полных 2024 токенах. Счётчик отвечает на
    вопрос «сколько токенов было в запросе», а не «сколько посчитали заново».

    Поэтому и сравнивать надо не два РАЗНЫХ запроса с общим системным
    промптом — у них одинаковая доля переиспользования, и разницы не будет
    видно, — а один и тот же запрос дважды подряд.

    Префикс делается уникальным (метка в системном промпте): иначе первый
    запрос попадёт в кэш от предыдущего прогона и «холодным» не будет.
    """
    print("\n" + "=" * 72)
    print("КЭШ ПРЕФИКСА: один и тот же запрос дважды подряд")
    print("=" * 72)

    marked = f"[проба кэша {time.time():.0f}]\n{system}"
    question = "Вопрос: что такое неустойка? Ответь одним предложением."

    cold = _report(
        "запрос 1 (префикс уникален — кэш пуст)", _ask(host, client, model, marked, question, opts)
    )
    warm = _report(
        "запрос 2 (тот же system И тот же user)", _ask(host, client, model, marked, question, opts)
    )
    other = _report(
        "запрос 3 (тот же system, ДРУГОЙ user)",
        _ask(
            host,
            client,
            model,
            marked,
            "Вопрос: что такое задаток? Ответь одним предложением.",
            opts,
        ),
    )

    print()
    if cold["read"] <= 0:
        print("  Ollama не отдала время чтения промпта — судить не по чему.")
        return
    # Стрелки и прочий не-cp1251 юникод здесь запрещены: консоль Windows
    # отдаёт stdout в кодовой странице системы, и один символ роняет скрипт
    # на последней строке, после того как все замеры уже сделаны.
    print(f"  токенов по счётчику: {cold['in']} -> {warm['in']} -> {other['in']} (не показатель)")
    print(
        f"  время чтения:        {cold['read']:.2f} с -> {warm['read']:.2f} с -> "
        f"{other['read']:.2f} с"
    )
    if warm["read"] < cold["read"] * 0.5:
        print(
            f"  ВЫВОД: кэш префикса РАБОТАЕТ — повтор дешевле в {cold['read'] / max(warm['read'], 1e-9):.0f} раз."
        )
        print("  ollama#14780 на этой сборке не воспроизводится.")
    else:
        print("  ВЫВОД: кэша префикса НЕТ — промпт читается заново каждый запрос.")
        print("  Это ollama#14780. Для нас цена мала: юр. анализ делает один вызов")
        print("  на часть договора, переиспользовать почти нечего.")


def _probe_num_ctx(
    host: str, client: httpx.Client, model: str, system: str, sizes: list[int]
) -> None:
    """Падает ли скорость ЧТЕНИЯ промпта от размера окна.

    Проверка гипотезы из бэклога: недостающие двадцать минут юр. анализа —
    обвал на длинном контексте. Один и тот же запрос гоняется в разных
    окнах; генерация обрезана до 16 токенов, чтобы мерить именно чтение.
    Смена num_ctx заставляет Ollama перезагрузить модель, поэтому загрузка
    считается отдельно и в вывод про скорость не входит.
    """
    print("\n" + "=" * 72)
    print("ЦЕНА ОКНА: то же чтение при разном num_ctx")
    print("=" * 72)
    question = "Вопрос: что такое неустойка? Ответь одним предложением."
    for size in sizes:
        marked = f"[проба окна {size} {time.time():.0f}]\n{system}"
        opts = {"temperature": 0, "num_ctx": size, "num_predict": 16}
        _report(f"num_ctx = {size}", _ask(host, client, model, marked, question, opts))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--model", default=None, help="По умолчанию — первая модель из /api/tags")
    parser.add_argument("--num-ctx", type=int, default=32768)
    parser.add_argument("--num-predict", type=int, default=1800)
    parser.add_argument("--skip-cache-probe", action="store_true")
    parser.add_argument(
        "--ctx-sweep",
        default="",
        help="Через запятую: окна для замера цены контекста, например 4096,8192,32768",
    )
    args = parser.parse_args()

    with httpx.Client(timeout=httpx.Timeout(connect=5.0, read=1800.0, write=30.0, pool=5.0)) as c:
        try:
            tags = c.get(f"{args.host}/api/tags").json().get("models", [])
        except httpx.HTTPError as e:
            print(f"Ollama недоступна на {args.host}: {e}", file=sys.stderr)
            return 1
        if not tags:
            print("В Ollama нет ни одной модели", file=sys.stderr)
            return 1
        model = args.model or tags[0]["name"]
        opts = {"temperature": 0, "num_ctx": args.num_ctx, "num_predict": args.num_predict}
        print(f"модель {model}, окно {args.num_ctx}, num_predict {args.num_predict}")

        system = _LEGAL_PROMPT.read_text(encoding="utf-8")
        contract = _CONTRACT.read_text(encoding="utf-8")

        print("\n" + "=" * 72)
        print("РАЗЛОЖЕНИЕ ВРЕМЕНИ: реальный промпт юр. анализа")
        print("=" * 72)
        # Метка времени в начале системного промпта: в бою юр. анализ приходит
        # на пустой кэш (документ у каждого свой), и замер обязан быть таким же.
        # Без метки первый же повторный прогон скрипта показал бы чужой,
        # недостижимо хороший результат.
        cold_system = f"[прогон {time.time():.0f}]\n{system}"
        stats = _ask(args.host, c, model, cold_system, f"ДОГОВОР:\n---\n{contract}\n---", opts)
        _report(f"договор {_CONTRACT.name}", stats)
        findings = stats.get("message", {}).get("content", "")
        print(f"  ответ: {len(findings)} символов")

        if not args.skip_cache_probe:
            _probe_cache(args.host, c, model, system, {**opts, "num_predict": 32})

        if args.ctx_sweep:
            _probe_num_ctx(args.host, c, model, system, [int(s) for s in args.ctx_sweep.split(",")])

    return 0


if __name__ == "__main__":
    sys.exit(main())
