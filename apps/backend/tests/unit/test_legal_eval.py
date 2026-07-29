"""Тесты метрики качества юр. анализа.

Правило сопоставления — самая спорная часть замера: если оно завышает, метрика
покажет несуществующий прогресс, если занижает — забракует работающий анализ.
Поэтому проверяется отдельно от прогона модели.
"""

from __future__ import annotations

from fire_safety_backend.services.legal_eval import (
    ContractScore,
    aggregate,
    match_finding,
    score_contract,
)

_ANCHOR = (
    "За несвоевременное выполнение работ Подрядчик уплачивает Заказчику "
    "неустойку в размере 2% за каждый день просрочки"
)
_RISK = {
    "id": "neustoyka_2_procenta",
    "anchor": _ANCHOR,
    "severity": "красный",
    "keywords": ["неустойк", "просрочк"],
    "why": "730 % годовых",
}


def _finding(quote: str, severity: str = "красный", risk_text: str = "тест") -> dict:
    return {
        "критичность": severity,
        "цитата_из_договора": quote,
        "в_чём_риск": risk_text,
        "предложение_правки": "",
    }


# --- Сопоставление находки с эталоном ---------------------------------------


def test_exact_quote_matches() -> None:
    assert match_finding(_finding(_ANCHOR), _RISK) is True


def test_whitespace_and_case_differences_do_not_break_matching() -> None:
    """Модель почти всегда возвращает цитату с другими переносами строк."""
    mangled = _ANCHOR.replace(" ", "\n  ").upper()
    assert match_finding(_finding(mangled), _RISK) is True


def test_quote_wider_than_anchor_matches() -> None:
    """Модель часто цитирует пункт целиком, захватив соседнее предложение."""
    wider = "5.2. " + _ANCHOR + " от суммы договора, без ограничения общей суммы."
    assert match_finding(_finding(wider), _RISK) is True


def test_quote_narrower_than_anchor_matches() -> None:
    assert match_finding(_finding(_ANCHOR[20:100]), _RISK) is True


def test_different_clause_does_not_match() -> None:
    other = "Заказчик обязуется обеспечить доступ бригады на объект в рабочее время."
    assert match_finding(_finding(other), _RISK) is False


def test_boilerplate_overlap_does_not_match() -> None:
    """Шаблонные обороты встречаются в договоре десятками — засчитывать их
    за попадание нельзя, иначе precision станет фикцией."""
    boilerplate = _finding("В случае нарушения условий настоящего Договора Стороны")
    assert match_finding(boilerplate, _RISK) is False


def test_keyword_fallback_matches_when_quote_is_a_neighbour_sentence() -> None:
    """Пункт про неустойку модель нередко цитирует предыдущей фразой про
    сроки — место не совпало, но риск найден верно."""
    finding = _finding(
        "Срок выполнения работ — 30 календарных дней.",
        risk_text="Неустойка начисляется за каждый день просрочки без верхнего предела",
    )
    assert match_finding(finding, _RISK) is True


def test_single_keyword_is_not_enough() -> None:
    """«неустойк» встречается в половине договора: по одному корню засчиталась
    бы любая находка про санкции."""
    finding = _finding("другой пункт", risk_text="здесь тоже упомянута неустойка")
    assert match_finding(finding, _RISK) is False


def test_empty_quote_and_empty_anchor() -> None:
    assert match_finding(_finding(""), _RISK) is False
    assert match_finding(_finding(_ANCHOR), {"id": "x", "anchor": "", "keywords": []}) is False


# --- Подсчёт по договору ----------------------------------------------------


_SECOND_RISK = {
    "id": "second",
    "anchor": "Оплата в течение 90 банковских дней",
    "severity": "красный",
    "keywords": ["оплат", "банковск"],
    "why": "длинная отсрочка",
}


def test_perfect_analysis_scores_one() -> None:
    risks = [_RISK, _SECOND_RISK]
    findings = [_finding(_ANCHOR), _finding("Оплата в течение 90 банковских дней")]
    score = score_contract("c.txt", findings, risks)
    assert score.precision == 1.0
    assert score.recall == 1.0
    assert score.f1 == 1.0
    assert score.missed == []
    assert score.spurious == []


def test_missed_risk_lowers_recall_only() -> None:
    score = score_contract("c.txt", [_finding(_ANCHOR)], [_RISK, _SECOND_RISK])
    assert score.recall == 0.5
    assert score.precision == 1.0
    assert score.missed == ["second"]


def test_spurious_finding_lowers_precision_only() -> None:
    findings = [_finding(_ANCHOR), _finding("Стороны обязуются соблюдать конфиденциальность")]
    score = score_contract("c.txt", findings, [_RISK])
    assert score.recall == 1.0
    assert score.precision == 0.5
    assert len(score.spurious) == 1


def test_two_findings_on_one_clause_are_not_counted_as_spurious() -> None:
    """Один пункт модель нередко разбирает двумя находками — отдельно срок,
    отдельно санкцию за его нарушение. Вторая находка не лишняя."""
    findings = [_finding(_ANCHOR), _finding(_ANCHOR + " от суммы договора")]
    score = score_contract("c.txt", findings, [_RISK])
    assert score.spurious == []
    assert score.precision == 1.0


def test_severity_understated_is_recorded() -> None:
    score = score_contract("c.txt", [_finding(_ANCHOR, severity="жёлтый")], [_RISK])
    assert score.severity_understated == ["neustoyka_2_procenta"]
    assert score.severity_exact == 0


def test_severity_overstated_is_recorded() -> None:
    green_risk = {**_RISK, "severity": "зелёный"}
    score = score_contract("c.txt", [_finding(_ANCHOR, severity="красный")], [green_risk])
    assert score.severity_overstated == ["neustoyka_2_procenta"]


def test_best_of_several_findings_decides_severity() -> None:
    """Если модель нашла пункт дважды и хоть раз назвала уровень верно,
    считать это ошибкой калибровки несправедливо."""
    findings = [_finding(_ANCHOR, severity="жёлтый"), _finding(_ANCHOR, severity="красный")]
    score = score_contract("c.txt", findings, [_RISK])
    assert score.severity_exact == 1
    assert score.severity_understated == []


def test_empty_analysis_scores_zero() -> None:
    score = score_contract("c.txt", [], [_RISK])
    assert score.precision == 0.0
    assert score.recall == 0.0
    assert score.f1 == 0.0
    assert score.missed == ["neustoyka_2_procenta"]


# --- Сводка по датасету -----------------------------------------------------


def test_aggregate_is_micro_averaged() -> None:
    """Микро-усреднение: договор с двадцатью рисками весит больше договора с
    одним, иначе короткий документ перекашивает картину."""
    big = ContractScore(
        contract="big", expected_total=20, found_total=20, matched=["x"] * 20, matched_findings=20
    )
    small = ContractScore(contract="small", expected_total=1, found_total=1, matched=[])
    summary = aggregate([big, small])
    assert summary["эталонных_рисков"] == 21
    assert summary["совпало"] == 20
    # Среднее из средних дало бы 0.5; микро-усреднение — 20/21.
    assert summary["recall"] == round(20 / 21, 3)


def test_aggregate_of_nothing_does_not_divide_by_zero() -> None:
    summary = aggregate([])
    assert summary["precision"] == 0.0
    assert summary["f1"] == 0.0
