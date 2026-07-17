#!/usr/bin/env python3
"""Бенчмарк локальных Ollama-моделей на трёх реалистичных задачах.

Самостоятельный скрипт (только httpx, без зависимости от backend/rag-пакетов
этого репозитория) — запускается вручную на боевом сервере, чтобы сравнить
модели по реальным цифрам (задержка первого токена, ток/с) ПЕРЕД тем, как
менять LLM_MODEL в конфиге. Промпты — сокращённые инлайн-версии трёх кнопок
приложения (spellcheck/legal/letter), без похода в resources/prompts/.

Запуск:
    python scripts/benchmark_models.py
    python scripts/benchmark_models.py --models qwen2.5:7b-instruct saiga_mistral_7b
    python scripts/benchmark_models.py --runs 3 --out /tmp/bench.json

По умолчанию тестирует все модели, установленные в Ollama (GET /api/tags).
Результат — таблица в stdout + scripts/benchmark_results.json (в git не
попадает — см. .gitignore, цифры зависят от конкретного железа).
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "benchmark_results.json"

TASKS = [
    {
        "name": "spellcheck",
        "system": (
            "Ты — редактор-корректор русского языка, специализируешься на "
            "официально-деловом стиле. Найди орфографические, пунктуационные "
            "и стилистические ошибки. Ответь СТРОГО валидным JSON: "
            '{"errors": [{"type": "орфография|пунктуация|стиль", "before": '
            '"...", "after": "...", "reason": "..."}], "corrected_text": "..."}'
        ),
        "user": (
            "Копания ООО «ПожСервис» уведомляет что срок выполнения работ "
            "по монтажу системы АПС переносится на две недели в связи "
            "с задержкой поставки оборудования от поставщика."
        ),
    },
    {
        "name": "legal",
        "system": (
            "Ты — юрист, специализирующийся на договорах в сфере пожарной "
            "безопасности. Найди риски в договоре для компании-исполнителя. "
            'Ответь СТРОГО валидным JSON: {"находки": [{"критичность": '
            '"красный|жёлтый|зелёный", "цитата_из_договора": "...", '
            '"в_чём_риск": "...", "ссылка_на_норму": "...", '
            '"предложение_правки": "..."}]}'
        ),
        "user": (
            "Договор подряда №14/2026. Штраф за просрочку выполнения работ "
            "составляет 10% от суммы договора за каждый день просрочки. "
            "Приёмка работ производится в течение 1 рабочего дня после "
            "уведомления подрядчиком о готовности."
        ),
    },
    {
        "name": "letter",
        "system": (
            "Ты составляешь официальные деловые письма от лица компании "
            "ООО «ПожСервис» по ГОСТ Р 7.0.97-2016. Ответь СТРОГО валидным "
            'JSON: {"тема": "...", "обращение": "...", "тело": "...", '
            '"формула_вежливости": "..."}'
        ),
        "user": (
            "Набросок: напомнить заказчику про плановое техобслуживание "
            "систем пожарной сигнализации в следующем месяце."
        ),
    },
]


@dataclass
class TaskResult:
    task: str
    ok: bool
    ttft_sec: float | None
    total_sec: float
    tokens: int
    tokens_per_sec: float | None
    error: str | None = None


@dataclass
class ModelResult:
    model: str
    tasks: list[TaskResult]

    @property
    def avg_ttft_sec(self) -> float | None:
        vals = [t.ttft_sec for t in self.tasks if t.ttft_sec is not None]
        return sum(vals) / len(vals) if vals else None

    @property
    def avg_tokens_per_sec(self) -> float | None:
        vals = [t.tokens_per_sec for t in self.tasks if t.tokens_per_sec is not None]
        return sum(vals) / len(vals) if vals else None

    @property
    def failed_count(self) -> int:
        return sum(1 for t in self.tasks if not t.ok)


def list_installed_models(host: str, client: httpx.Client) -> list[str]:
    r = client.get(f"{host}/api/tags", timeout=10)
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]


def run_task(host: str, client: httpx.Client, model: str, task: dict) -> TaskResult:
    payload = {
        "model": model,
        "stream": True,
        "format": "json",
        "messages": [
            {"role": "system", "content": task["system"]},
            {"role": "user", "content": task["user"]},
        ],
        "options": {"temperature": 0.2, "num_ctx": 4096, "num_predict": 400},
    }
    start = time.monotonic()
    first_token_at: float | None = None
    tokens = 0
    try:
        with client.stream("POST", f"{host}/api/chat", json=payload, timeout=180) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                content = chunk.get("message", {}).get("content", "")
                if content:
                    if first_token_at is None:
                        first_token_at = time.monotonic()
                    tokens += 1
    except (httpx.HTTPError, json.JSONDecodeError) as e:
        return TaskResult(
            task=task["name"],
            ok=False,
            ttft_sec=None,
            total_sec=time.monotonic() - start,
            tokens=0,
            tokens_per_sec=None,
            error=str(e),
        )

    total = time.monotonic() - start
    ttft = (first_token_at - start) if first_token_at is not None else None
    gen_time = (total - ttft) if ttft is not None else total
    tps = tokens / gen_time if gen_time > 0 and tokens > 0 else None
    return TaskResult(
        task=task["name"],
        ok=tokens > 0,
        ttft_sec=ttft,
        total_sec=total,
        tokens=tokens,
        tokens_per_sec=tps,
    )


def benchmark_model(host: str, client: httpx.Client, model: str, runs: int) -> ModelResult:
    results: list[TaskResult] = []
    for task in TASKS:
        for run_i in range(runs):
            print(f"  {model} · {task['name']} ({run_i + 1}/{runs})...", end=" ", flush=True)
            res = run_task(host, client, model, task)
            print("ок" if res.ok else f"ошибка: {res.error}")
            results.append(res)
    return ModelResult(model=model, tasks=results)


def print_table(results: list[ModelResult]) -> None:
    header = f"{'Модель':<32} {'TTFT, с':>10} {'Ток/с':>10} {'Провалы':>10}"
    print()
    print(header)
    print("-" * len(header))
    for mr in results:
        ttft = f"{mr.avg_ttft_sec:.2f}" if mr.avg_ttft_sec is not None else "—"
        tps = f"{mr.avg_tokens_per_sec:.1f}" if mr.avg_tokens_per_sec is not None else "—"
        print(f"{mr.model:<32} {ttft:>10} {tps:>10} {mr.failed_count:>10}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST, help="Ollama host (по умолчанию %(default)s)"
    )
    parser.add_argument(
        "--models",
        nargs="+",
        default=None,
        help="Модели для теста (по умолчанию — все установленные)",
    )
    parser.add_argument(
        "--runs", type=int, default=1, help="Число повторов на задачу (по умолчанию %(default)s)"
    )
    parser.add_argument("--out", default=str(DEFAULT_OUTPUT), help="Куда писать JSON-результат")
    args = parser.parse_args()

    with httpx.Client() as client:
        models = args.models or list_installed_models(args.host, client)
        if not models:
            print("Не найдено ни одной установленной модели (ollama pull <модель>)")
            return

        print(
            f"Бенчмарк {len(models)} модел(и/ей) × {len(TASKS)} задачи × {args.runs} "
            f"прогон(ов): {', '.join(models)}"
        )
        results = [benchmark_model(args.host, client, m, args.runs) for m in models]

    print_table(results)

    out_path = Path(args.out)
    out_path.write_text(
        json.dumps(
            [{"model": r.model, "tasks": [asdict(t) for t in r.tasks]} for r in results],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Результат сохранён: {out_path}")


if __name__ == "__main__":
    main()
