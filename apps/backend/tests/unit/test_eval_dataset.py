"""Проверка целостности размеченного датасета договоров.

Метрика опирается на то, что поле `anchor` — ТОЧНАЯ подстрока договора. Стоит
кому-нибудь поправить формулировку в тексте договора, не тронув разметку, и
риск станет ненаходимым: recall упадёт, а причина будет выглядеть как
деградация модели. Поэтому связь проверяется тестом, а не на доверии.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_DATASET = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "contracts"


def _cases() -> list[tuple[Path, Path]]:
    return [
        (p, p.with_name(p.name.replace(".expected.json", ".txt")))
        for p in sorted(_DATASET.glob("*.expected.json"))
    ]


def test_dataset_is_not_empty() -> None:
    assert len(_cases()) >= 3, "датасет должен содержать хотя бы три договора"


@pytest.mark.parametrize("expected_path,contract_path", _cases(), ids=lambda p: p.name)
def test_every_anchor_is_an_exact_substring(expected_path: Path, contract_path: Path) -> None:
    assert contract_path.exists(), f"нет договора для разметки {expected_path.name}"
    text = contract_path.read_text(encoding="utf-8")
    risks = json.loads(expected_path.read_text(encoding="utf-8"))["risks"]
    assert risks, "разметка без рисков бесполезна"
    for risk in risks:
        assert risk["anchor"] in text, (
            f"{expected_path.name}: якорь риска {risk['id']!r} не найден в тексте договора"
        )


@pytest.mark.parametrize("expected_path,contract_path", _cases(), ids=lambda p: p.name)
def test_risk_ids_are_unique_and_severities_valid(expected_path: Path, contract_path: Path) -> None:
    risks = json.loads(expected_path.read_text(encoding="utf-8"))["risks"]
    ids = [r["id"] for r in risks]
    assert len(ids) == len(set(ids)), f"{expected_path.name}: повторяющиеся id рисков"
    for risk in risks:
        assert risk["severity"] in {"красный", "жёлтый", "зелёный"}, risk["id"]
        assert risk["keywords"], f"{risk['id']}: без ключевых слов не сработает запасной путь"


def test_balanced_contract_has_no_red_risks() -> None:
    """05_sbalansirovannyy — контроль на склонность преувеличивать: договор,
    который рынок подписывает без правок. Если разметка сама поставит здесь
    красный, тест на ложные срабатывания потеряет смысл."""
    path = _DATASET / "05_sbalansirovannyy.expected.json"
    risks = json.loads(path.read_text(encoding="utf-8"))["risks"]
    assert all(r["severity"] != "красный" for r in risks)
