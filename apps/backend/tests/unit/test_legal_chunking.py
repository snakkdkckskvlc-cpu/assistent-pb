"""Тесты нарезки договора под окно модели.

Регрессия на реальный баг: юр. анализ отправлял договор целиком, Ollama при
превышении num_ctx молча отбрасывала начало запроса ВМЕСТЕ С СИСТЕМНЫМ
ПРОМПТОМ, и модель возвращала не анализ рисков, а пересказ хвоста документа.
Замер на живом прогоне: из ~14 000 отправленных токенов обрабатывалось 4 098.
"""

from __future__ import annotations

import pytest
from fire_safety_backend import config
from fire_safety_backend.pipelines import legal


def test_part_budget_fits_into_window() -> None:
    """Бюджет на текст + ответ + промпт + нормы обязан помещаться в окно."""
    prompt = "П" * 3000
    budget_tokens = legal._input_budget_tokens(prompt)
    reserved = (
        config.LLM_NUM_PREDICT_LEGAL_PART
        + legal._SAFETY_TOKENS
        + legal._estimate_tokens(prompt)
        + legal._RAG_CHUNKS_PER_PART * int(legal._RAG_CHUNK_MAX_CHARS / legal._CHARS_PER_TOKEN)
    )
    assert budget_tokens + reserved <= config.LLM_NUM_CTX_LEGAL


def test_estimate_is_conservative_versus_measured_density() -> None:
    """Оценка должна быть ПЕССИМИСТИЧНЕЕ замеренной плотности.

    Замерено на договорах НЛМК: 2.57 символа на токен. Если константа станет
    больше замеренного, оценка начнёт занижать реальный расход и часть снова
    сможет не влезть в окно.
    """
    assert legal._CHARS_PER_TOKEN < 2.57
    # Замерено 3.78 токена на слово, но на таблицах реквизитов доходит до 4.67.
    assert legal._TOKENS_PER_WORD >= 4.6


def test_oversized_part_is_split_further() -> None:
    """Страховка поверх расчёта по словам: кусок, не влезающий по символам,
    обязан быть раздроблен, а не отправлен как есть."""
    prompt = "П" * 1000
    budget = legal._input_budget_tokens(prompt)
    huge = "слово " * (budget * 3)  # заведомо больше бюджета

    parts = legal._split_oversized_parts([huge], prompt)

    assert len(parts) > 1
    for p in parts:
        assert legal._estimate_tokens(p) <= budget, "часть всё ещё не влезает в окно"


def test_small_parts_are_not_split() -> None:
    prompt = "П" * 1000
    small = "короткий пункт договора"
    assert legal._split_oversized_parts([small], prompt) == [small]


def test_splitting_stops_at_minimum_size() -> None:
    """Дробление не должно уходить в бесконечность на патологическом входе:
    ниже минимального размера кусок оставляем как есть."""
    prompt = "П" * 1000
    # Слово длиной с целый бюджет — сколько ни дроби, по символам не влезет.
    monstrous = "ы" * (legal._input_budget_tokens(prompt) * 10)
    parts = legal._split_oversized_parts([monstrous], prompt)
    assert len(parts) == 1


def test_merge_findings_drops_duplicates_across_parts() -> None:
    """Один и тот же пункт может всплыть в двух соседних частях — показывать
    его дважды не нужно."""
    a = [{"цитата_из_договора": "неустойка  2%  за каждый день", "в_чём_риск": "много"}]
    b = [
        {"цитата_из_договора": "Неустойка 2% за КАЖДЫЙ день", "в_чём_риск": "дубль"},
        {"цитата_из_договора": "оплата 60 дней", "в_чём_риск": "кассовый разрыв"},
    ]
    merged = legal._merge_findings([a, b])
    assert len(merged) == 2
    assert merged[0]["в_чём_риск"] == "много"


def test_merge_findings_keeps_distinct_without_quotes() -> None:
    a = [{"в_чём_риск": "риск один"}]
    b = [{"в_чём_риск": "риск два"}]
    assert len(legal._merge_findings([a, b])) == 2


def test_merge_summaries_dedups_and_joins() -> None:
    s1 = {
        "плюсы_для_компании": ["гарантия 12 мес"],
        "минусы_для_компании": ["штрафы"],
        "общий_вывод": "Требует правок.",
    }
    s2 = {
        "плюсы_для_компании": ["Гарантия 12 мес"],
        "минусы_для_компании": ["нет лимита"],
        "общий_вывод": "Требует правок.",
    }
    merged = legal._merge_summaries([s1, s2])
    assert merged["плюсы_для_компании"] == ["гарантия 12 мес"]
    assert set(merged["минусы_для_компании"]) == {"штрафы", "нет лимита"}
    assert merged["общий_вывод"] == "Требует правок."


def test_merge_summaries_survives_garbage() -> None:
    assert legal._merge_summaries([None, "строка", {}])["плюсы_для_компании"] == []


async def test_run_legal_analysis_splits_and_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сквозной тест: длинный договор уходит НЕСКОЛЬКИМИ запросами, находки
    из всех частей попадают в общий результат."""
    calls: list[str] = []

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        calls.append(user)
        idx = len(calls)
        return {
            "находки": [
                {
                    "критичность": "красный",
                    "цитата_из_договора": f"пункт {idx}",
                    "в_чём_риск": f"риск {idx}",
                }
            ],
            "сводка": {"плюсы_для_компании": [], "минусы_для_компании": [], "общий_вывод": "ok"},
        }

    monkeypatch.setattr(legal.llm, "chat_json", fake_chat_json)
    monkeypatch.setattr(
        legal, "retrieve_many", lambda queries, top_k=None, where=None: [[] for _ in queries]
    )
    monkeypatch.setattr(
        legal, "retrieve_hybrid", lambda queries, top_k=None, where=None: [[] for _ in queries]
    )

    # Договор заведомо больше одного окна.
    text = "Пункт договора об ответственности сторон. " * 3000
    result = await legal.run_legal_analysis(text)

    # Частей несколько + ОДИН финальный проход по договору целиком: он видит
    # системные перекосы, невыводимые из отдельной части.
    assert len(calls) > 2, "длинный договор обязан уйти несколькими запросами"
    assert result["_частей"] == len(calls) - 1
    assert len(result["находки"]) == result["_частей"]
    final_call = calls[-1]
    assert "ОГЛАВЛЕНИЕ ДОГОВОРА" in final_call
    assert "КАРТА САНКЦИЙ" in final_call
    # Каждый запрос обязан нести системный промпт и не превышать окно.
    for user_msg in calls:
        assert legal._estimate_tokens(user_msg) <= config.LLM_NUM_CTX_LEGAL


# --- Финальный проход: скелет договора извлекается регэкспом, не моделью ---

_CONTRACT = """1. ПРЕДМЕТ ДОГОВОРА
1.1. Подрядчик выполняет работы по техническому обслуживанию.

3. ЦЕНА И ПОРЯДОК РАСЧЕТОВ
3.6. Заказчик оплачивает работы в течение 60 календарных дней.

6. ОТВЕТСТВЕННОСТЬ СТОРОН
6.1. За несвоевременное выполнение работ Подрядчик уплачивает Заказчику неустойку 2% за каждый день просрочки.
6.2. За некачественный ремонт Подрядчик уплачивает Заказчику штраф 20% стоимости работ.
6.7. Заказчик вправе взыскать с Подрядчика убытки в полном размере сверх неустойки.
"""


def test_outline_extracted_from_real_structure() -> None:
    outline = legal._build_outline(_CONTRACT)
    joined = "\n".join(outline)
    assert "1 ПРЕДМЕТ ДОГОВОРА" in joined
    assert "6 ОТВЕТСТВЕННОСТЬ СТОРОН" in joined
    assert "3.6 Заказчик оплачивает" in joined


def test_sanction_map_finds_all_penalties() -> None:
    """Карта санкций — прямой источник пропущенного системного вывода:
    все санкции в разделе 6 наложены на Подрядчика."""
    sanctions = legal._build_sanction_map(_CONTRACT)
    assert len(sanctions) == 3
    joined = "\n".join(sanctions)
    assert "п. 6.1" in joined
    assert "п. 6.2" in joined
    assert "п. 6.7" in joined
    # В каждом пункте про санкции фигурирует Подрядчик.
    assert all("Подрядчик" in s for s in sanctions)


def test_sanction_map_ignores_clauses_without_penalties() -> None:
    sanctions = legal._build_sanction_map(_CONTRACT)
    assert not any("1.1" in s for s in sanctions)
    assert not any("ПРЕДМЕТ" in s for s in sanctions)


def test_condense_findings_respects_budget() -> None:
    findings = [
        {"критичность": "красный", "в_чём_риск": "риск " * 40, "ссылка_на_норму": "ст. 333 ГК РФ"}
        for _ in range(20)
    ]
    condensed = legal._condense_findings(findings, 500)
    assert len(condensed) <= 700  # бюджет + длина последней строки
    assert "красный" in condensed


async def test_final_pass_timeout_does_not_lose_findings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Зависший финальный проход не должен утаскивать за собой весь результат.

    Регрессия с живого прогона: этот вызов завис и держал задачу 5.7 часа
    сверх 40 минут основного разбора — read-таймаут httpx считает паузу МЕЖДУ
    байтами и при медленной потоковой генерации не срабатывает никогда.
    """
    import asyncio

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        if "ОГЛАВЛЕНИЕ ДОГОВОРА" in user:
            await asyncio.sleep(60)  # финальный проход «завис»
        return {
            "находки": [{"критичность": "красный", "цитата_из_договора": f"п. {len(user)}"}],
            "сводка": {"плюсы_для_компании": [], "минусы_для_компании": [], "общий_вывод": "ok"},
        }

    monkeypatch.setattr(legal.llm, "chat_json", fake_chat_json)
    monkeypatch.setattr(
        legal, "retrieve_many", lambda queries, top_k=None, where=None: [[] for _ in queries]
    )
    monkeypatch.setattr(
        legal, "retrieve_hybrid", lambda queries, top_k=None, where=None: [[] for _ in queries]
    )
    monkeypatch.setattr(legal, "_FINAL_PASS_TIMEOUT_SEC", 0.2)

    result = await legal.run_legal_analysis("Пункт договора об ответственности. " * 3000)

    # Находки частей на месте, несмотря на провалившийся финальный проход.
    assert result["находки"]
    assert "системные_выводы" not in (result["сводка"] or {})


# --- Автоподбор окна под память машины --------------------------------------
# Раньше окно было жёстко 8192 — под 8 ГБ машины разработчика. Боевой сервер со
# 128 ГБ работал в том же тесном режиме: договор дробился на восемь частей
# вместо одной, а платим мы за каждый ВЫДАННЫЙ токен (замер: чтение промпта
# 165–260 токенов/с, генерация 11–12,5).


def test_auto_window_grows_with_ram() -> None:
    from fire_safety_backend import config as config_module

    assert config_module._auto_num_ctx_legal.__doc__  # функция на месте
    sizes = {}
    for ram, expected in ((8, 8192), (16, 12288), (32, 16384), (128, 32768)):
        sizes[ram] = expected
    # Проверяем саму лестницу порогов через подмену измерителя памяти.
    import pytest as _pytest

    for ram, expected in sizes.items():
        with _pytest.MonkeyPatch.context() as mp:
            mp.setattr(config_module, "_total_ram_gb", lambda ram=ram: float(ram))
            assert config_module._auto_num_ctx_legal() == expected, f"{ram} ГБ"


def test_auto_window_falls_back_when_ram_unknown() -> None:
    """Не смогли определить память — берём заведомо безопасное значение, а не
    самое большое: на слабой машине большое окно уводит всё в swap."""
    import pytest as _pytest
    from fire_safety_backend import config as config_module

    with _pytest.MonkeyPatch.context() as mp:
        mp.setattr(config_module, "_total_ram_gb", lambda: 0.0)
        assert config_module._auto_num_ctx_legal() == 8192


def test_single_part_gets_the_full_answer_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Когда договор влез целиком, находки по ВСЕМУ документу должны уместиться
    в один ответ — урезанного «на часть» резерва не хватит."""
    import asyncio

    calls: list[dict] = []

    async def fake_chat_json(system: str, user: str, **kwargs) -> dict:
        calls.append(kwargs)
        return {"находки": [], "сводка": {}}

    monkeypatch.setattr(legal.llm, "chat_json", fake_chat_json)
    monkeypatch.setattr(
        legal, "retrieve_hybrid", lambda queries, top_k=None, where=None: [[] for _ in queries]
    )
    monkeypatch.setattr(
        legal, "retrieve_many", lambda queries, top_k=None, where=None: [[] for _ in queries]
    )

    asyncio.run(legal.run_legal_analysis("Короткий договор в одну часть."))
    assert len(calls) == 1, "короткий текст не должен дробиться"
    assert calls[0]["num_predict"] == config.LLM_NUM_PREDICT_LEGAL
