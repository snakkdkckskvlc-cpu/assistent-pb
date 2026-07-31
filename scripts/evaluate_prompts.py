#!/usr/bin/env python3
"""Замер качества юр. анализа на размеченном датасете: precision / recall / F1.

Датасет — `apps/backend/tests/fixtures/contracts/`: договор `NN_имя.txt` плюс
разметка `NN_имя.expected.json` со списком заложенных рисков. Каждый риск
привязан к ТОЧНОЙ подстроке договора (`anchor`), поэтому засчитывается не
совпадение формулировок, а попадание в нужное место документа (правило
сопоставления — `services/legal_eval.py`, оно покрыто тестами).

Запуск (из venv проекта):
    python scripts/evaluate_prompts.py --mock     # без Ollama: проверить сам замер
    python scripts/evaluate_prompts.py            # реальный прогон через Ollama
    python scripts/evaluate_prompts.py --only 05  # один договор

ВНИМАНИЕ по времени. Реальный прогон идёт через ту же модель, что и продукт:
на CPU это единицы минут на договор, весь датасет — десятки минут. Режим
--mock отвечает мгновенно и нужен, чтобы убедиться, что сам замер считает
верно (на канонических ответах он обязан дать precision = recall = 1.0), а не
чтобы оценивать модель.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

# Скрипт мог быть запущен системным python — тогда зависимостей приложения
# в нём нет, и первый же импорт упал бы с невнятным ModuleNotFoundError.
# Перезапускаемся интерпретатором venv.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _venv import ensure_venv  # noqa: E402

ensure_venv()


_ROOT = Path(__file__).resolve().parent.parent
_DATASET = _ROOT / "apps" / "backend" / "tests" / "fixtures" / "contracts"
_DEFAULT_OUT = _ROOT / "data" / "eval_results.json"


def _load_dataset(only: str | None) -> list[tuple[Path, dict]]:
    if not _DATASET.exists():
        raise SystemExit(f"Датасет не найден: {_DATASET}")
    items: list[tuple[Path, dict]] = []
    for expected_path in sorted(_DATASET.glob("*.expected.json")):
        contract_path = expected_path.with_name(
            expected_path.name.replace(".expected.json", ".txt")
        )
        if not contract_path.exists():
            print(f"! нет договора для разметки {expected_path.name}", file=sys.stderr)
            continue
        if only and not contract_path.name.startswith(only):
            continue
        items.append((contract_path, json.loads(expected_path.read_text(encoding="utf-8"))))
    if not items:
        raise SystemExit("Нечего прогонять — датасет пуст или фильтр --only ничего не выбрал")
    return items


def _install_mock_llm(expected_by_contract: dict[str, list[dict]]) -> None:
    """Канонический «идеальный» ответ модели, собранный из самой разметки.

    Нужен ровно для одного: убедиться, что метрика считает верно. Если на
    таких ответах precision или recall не равны 1.0, сломан замер, а не модель.
    """
    from fire_safety_backend.infrastructure import llm
    from fire_safety_backend.pipelines import legal as legal_module

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        findings = []
        for risks in expected_by_contract.values():
            for risk in risks:
                anchor = risk.get("anchor", "")
                # Отдаём находку только если её место реально попало в этот
                # кусок договора — иначе получился бы не эталон, а подсказка.
                if anchor and anchor in user:
                    findings.append(
                        {
                            "критичность": risk.get("severity", "жёлтый"),
                            "цитата_из_договора": anchor,
                            "в_чём_риск": risk.get("why", ""),
                            "ссылка_на_норму": "требует проверки юристом",
                            "источник_фрагмента": "",
                            "предложение_правки": "—",
                        }
                    )
        return {
            "находки": findings,
            "сводка": {
                "плюсы_для_компании": [],
                "минусы_для_компании": [],
                "общий_вывод": "тестовый прогон",
            },
        }

    llm.chat_json = fake_chat_json
    legal_module.retrieve_hybrid = lambda queries, top_k=None, where=None, domain=None: [
        [] for _ in queries
    ]
    legal_module.retrieve_many = lambda queries, top_k=None, where=None, domain=None: [
        [] for _ in queries
    ]


# Промпт «как если бы человек просто открыл Ollama». Намеренно не соломенное
# чучело: роль задана, сторона указана, формат ответа тот же — иначе метрика
# сравнивала бы не качество разбора, а умение попасть в схему. Нет ровно того,
# что даёт наш пайплайн: признаков критичности, нормативной базы, нарезки под
# окно, проверки ссылок и эскалации по цифрам.
_BARE_PROMPT = """Ты юрист. Проанализируй договор со стороны ПОДРЯДЧИКА (исполнителя) и найди риски для него.

Ответь строго валидным JSON без markdown:
{
  "находки": [
    {
      "критичность": "красный" | "жёлтый" | "зелёный",
      "цитата_из_договора": "точная цитата из текста договора",
      "в_чём_риск": "объяснение в 1-3 предложениях",
      "ссылка_на_норму": "статья закона РФ или «требует проверки юристом»",
      "предложение_правки": "альтернативная формулировка"
    }
  ],
  "сводка": {"плюсы_для_компании": [], "минусы_для_компании": [], "общий_вывод": ""}
}"""


async def _analyse_bare(text: str) -> dict:
    """Один запрос к той же модели без промпта продукта, RAG и нарезки.

    Это база для сравнения: показывает, что даёт сама модель, и, значит, чего
    стоит вся обвязка вокруг неё.
    """
    from fire_safety_backend import config
    from fire_safety_backend.infrastructure import llm

    return await llm.chat_json(
        system=_BARE_PROMPT,
        user=f"ДОГОВОР:\n---\n{text}\n---",
        num_ctx=config.LLM_NUM_CTX_LEGAL,
        num_predict=config.LLM_NUM_PREDICT_LEGAL,
    )


async def _run(
    items: list[tuple[Path, dict]], out_path: Path, mock: bool, bare: bool = False
) -> int:
    from fire_safety_backend.infrastructure import llm
    from fire_safety_backend.pipelines.legal import run_legal_analysis
    from fire_safety_backend.services.legal_eval import aggregate, score_contract

    # HTTP-клиент к Ollama в бою поднимает FastAPI-lifespan; здесь сервера нет,
    # поэтому поднимаем его руками — иначе первый же вызов упадёт с «LLM-клиент
    # не запущен». В режиме --mock клиент не нужен вовсе.
    if not mock:
        llm.startup()
    analyse = _analyse_bare if bare else run_legal_analysis
    try:
        return await _score_all(items, out_path, analyse, aggregate, score_contract)
    finally:
        if not mock:
            await llm.shutdown()


async def _score_all(items, out_path, run_legal_analysis, aggregate, score_contract) -> int:
    scores = []
    raw_findings: dict[str, list] = {}
    for contract_path, expected in items:
        text = contract_path.read_text(encoding="utf-8")
        risks = expected.get("risks", [])
        print(f"→ {contract_path.name}: {len(risks)} эталонных рисков, {len(text.split())} слов")
        started = time.time()
        try:
            result = await run_legal_analysis(text)
        except Exception as e:  # noqa: BLE001 — один упавший договор не должен рвать замер
            print(f"  ОШИБКА: {e}", file=sys.stderr)
            continue
        elapsed = time.time() - started
        findings = result.get("находки", [])
        score = score_contract(contract_path.name, findings, risks)
        score.elapsed_sec = elapsed
        scores.append(score)
        # Сами находки — рядом с метриками. Без них разбор промаха требует
        # повторного прогона на полчаса, а по агрегату не видно, модель
        # промолчала или сказала не то.
        raw_findings[contract_path.name] = findings
        print(
            f"  находок {score.found_total}, совпало {len(score.matched)}/{score.expected_total}, "
            f"P={score.precision:.2f} R={score.recall:.2f} F1={score.f1:.2f} ({elapsed:.0f}с)"
        )

    if not scores:
        print("Ни один договор не отработал", file=sys.stderr)
        return 1

    _print_table(scores)
    summary = aggregate(scores)
    print("\nИТОГО:")
    for key, value in summary.items():
        print(f"  {key:32} {value}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "сводка": summary,
                "по_договорам": [s.as_dict() for s in scores],
                "находки": raw_findings,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"\nПодробности: {out_path}")
    return 0


def _print_table(scores) -> None:
    print(
        f"\n{'договор':34} {'эталон':>7} {'найдено':>8} {'совпало':>8} {'P':>6} {'R':>6} {'F1':>6}"
    )
    print("-" * 80)
    for s in scores:
        print(
            f"{s.contract[:34]:34} {s.expected_total:7} {s.found_total:8} {len(s.matched):8} "
            f"{s.precision:6.2f} {s.recall:6.2f} {s.f1:6.2f}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Замер качества юр. анализа на датасете")
    parser.add_argument("--mock", action="store_true", help="Без Ollama: проверить сам замер")
    parser.add_argument("--only", default=None, help="Префикс имени файла договора")
    parser.add_argument("--out", type=Path, default=_DEFAULT_OUT, help="Куда писать результаты")
    parser.add_argument(
        "--bare",
        action="store_true",
        help="Голая модель: один запрос без промпта продукта, RAG, нарезки и проверок",
    )
    args = parser.parse_args()

    sys.path.insert(0, str(_ROOT / "apps" / "backend" / "src"))
    sys.path.insert(0, str(_ROOT / "packages" / "rag" / "src"))

    items = _load_dataset(args.only)
    if args.mock:
        _install_mock_llm({str(p): e.get("risks", []) for p, e in items})
        print("Режим --mock: канонические ответы, Ollama не вызывается.")
        print("Ожидание: precision = recall = 1.0. Иначе сломан сам замер.\n")

    if args.bare:
        print("Режим --bare: та же модель, но БЕЗ промпта продукта, RAG,")
        print("нарезки на части, проверки ссылок и эскалации критичности.\n")
    return asyncio.run(_run(items, args.out, args.mock, args.bare))


if __name__ == "__main__":
    sys.exit(main())
