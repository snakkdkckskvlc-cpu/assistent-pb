"""Метрика качества юр. анализа: сопоставление находок с эталоном.

Зачем отдельным модулем, а не внутри скрипта: правило сопоставления — самая
спорная часть замера, и его надо покрывать тестами наравне с продуктовым
кодом. `scripts/evaluate_prompts.py` — тонкий CLI поверх этого модуля.

Как сопоставляются находка и эталонный риск. Сравнивать формулировки
бессмысленно: модель каждый раз пишет «в чём риск» своими словами, и любое
сравнение текстов превратится в замер синонимии, а не качества анализа.
Поэтому эталон привязан к МЕСТУ В ДОГОВОРЕ — точной подстроке (`anchor`), а
находка засчитывается, если её цитата перекрывается с этим местом. Место в
документе объективно: либо модель показала на этот пункт, либо нет.

Запасной путь — по корням ключевых слов в «в_чём_риск». Он нужен, потому что
один и тот же риск может быть процитирован соседним предложением: пункт про
неустойку часто цитируют вместе с предыдущей фразой про сроки.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Минимальная длина общей подстроки, при которой считаем, что цитата и якорь
# указывают на одно место. Короче — начнут совпадать шаблонные обороты
# («в случае нарушения», «настоящего Договора»), которых в договоре десятки.
_MIN_OVERLAP_CHARS = 40


def _normalize(text: str) -> str:
    """Схлопывает пробелы и регистр: цитата модели почти всегда отличается от
    оригинала переносами строк и неразрывными пробелами."""
    return re.sub(r"\s+", " ", str(text or "")).strip().casefold()


def _overlaps(quote: str, anchor: str) -> bool:
    """Указывают ли цитата и якорь на одно место договора."""
    q, a = _normalize(quote), _normalize(anchor)
    if not q or not a:
        return False
    if a in q or q in a:
        return True
    # Частичное перекрытие: модель процитировала кусок, захватив соседнее
    # предложение и обрезав конец. Ищем общий фрагмент достаточной длины.
    shorter, longer = (q, a) if len(q) <= len(a) else (a, q)
    step = max(1, _MIN_OVERLAP_CHARS // 4)
    for start in range(0, len(shorter) - _MIN_OVERLAP_CHARS + 1, step):
        if shorter[start : start + _MIN_OVERLAP_CHARS] in longer:
            return True
    return False


def _keywords_hit(finding: dict, keywords: list[str]) -> bool:
    """Все корни из эталона встречаются в тексте находки.

    Требуются ВСЕ, а не любой: «неустойк» встречается в половине договора, и
    по одному корню засчиталась бы любая находка про санкции.
    """
    if not keywords:
        return False
    haystack = _normalize(
        f"{finding.get('в_чём_риск', '')} {finding.get('цитата_из_договора', '')} "
        f"{finding.get('предложение_правки', '')}"
    )
    return all(_normalize(k) in haystack for k in keywords)


def match_finding(finding: dict, risk: dict) -> bool:
    return _overlaps(
        finding.get("цитата_из_договора", ""), risk.get("anchor", "")
    ) or _keywords_hit(finding, risk.get("keywords", []))


_SEVERITY_ORDER = {"зелёный": 0, "жёлтый": 1, "красный": 2}


def _severity_rank(value: str) -> int:
    return _SEVERITY_ORDER.get(_normalize(value), -1)


@dataclass
class ContractScore:
    """Результат по одному договору."""

    contract: str
    expected_total: int = 0
    found_total: int = 0
    matched: list[str] = field(default_factory=list)
    # Сколько находок указали хоть на один эталонный риск. Считается отдельно
    # от `matched`: один пункт договора модель нередко разбирает двумя
    # находками, и если мерить точность числом совпавших РИСКОВ, две верные
    # находки на один пункт уронят precision до 0.5 без единой ошибки.
    matched_findings: int = 0
    missed: list[str] = field(default_factory=list)
    spurious: list[str] = field(default_factory=list)
    severity_exact: int = 0
    severity_understated: list[str] = field(default_factory=list)
    severity_overstated: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0

    @property
    def precision(self) -> float:
        """Доля находок модели, попавших хоть в какой-то эталонный риск."""
        if not self.found_total:
            return 0.0
        return self.matched_findings / self.found_total

    @property
    def recall(self) -> float:
        if not self.expected_total:
            return 0.0
        return len(self.matched) / self.expected_total

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def as_dict(self) -> dict:
        return {
            "договор": self.contract,
            "эталонных_рисков": self.expected_total,
            "находок_модели": self.found_total,
            "совпало": len(self.matched),
            "находок_в_цель": self.matched_findings,
            "precision": round(self.precision, 3),
            "recall": round(self.recall, 3),
            "f1": round(self.f1, 3),
            "пропущено": self.missed,
            "лишние_находки": self.spurious,
            "критичность_точно": self.severity_exact,
            "критичность_занижена": self.severity_understated,
            "критичность_завышена": self.severity_overstated,
            "секунд": round(self.elapsed_sec, 1),
        }


def score_contract(
    contract: str, findings: list[dict], expected_risks: list[dict]
) -> ContractScore:
    """Сопоставляет находки анализа с эталонной разметкой одного договора.

    Сопоставление «многие ко многим» по построению: один пункт договора модель
    нередко разбирает двумя находками (например, отдельно срок и отдельно
    санкцию за его нарушение), и объявлять вторую находку лишней было бы
    неверно. Поэтому:
      * эталонный риск считается найденным, если на него указала хоть одна находка;
      * находка считается лишней, только если она не указала НИ НА ОДИН риск.
    """
    score = ContractScore(contract=contract)
    score.expected_total = len(expected_risks)
    score.found_total = len(findings)

    matched_findings: set[int] = set()
    for risk in expected_risks:
        hits = [i for i, f in enumerate(findings) if match_finding(f, risk)]
        risk_id = str(risk.get("id", "?"))
        if not hits:
            score.missed.append(risk_id)
            continue
        score.matched.append(risk_id)
        matched_findings.update(hits)

        # Критичность оценивается по ЛУЧШЕЙ из указавших на риск находок:
        # если модель нашла пункт дважды и хоть раз назвала уровень верно,
        # считать это ошибкой калибровки несправедливо.
        expected_rank = _severity_rank(str(risk.get("severity", "")))
        actual_rank = max(_severity_rank(str(findings[i].get("критичность", ""))) for i in hits)
        if actual_rank == expected_rank:
            score.severity_exact += 1
        elif actual_rank < expected_rank:
            score.severity_understated.append(risk_id)
        else:
            score.severity_overstated.append(risk_id)

    score.matched_findings = len(matched_findings)
    score.spurious = [
        str(f.get("цитата_из_договора", ""))[:80]
        for i, f in enumerate(findings)
        if i not in matched_findings
    ]
    return score


def aggregate(scores: list[ContractScore]) -> dict:
    """Сводные метрики по всему датасету.

    Микро-усреднение (по сумме находок), а не среднее из средних: договор с
    двадцатью рисками должен весить больше договора с четырьмя, иначе один
    короткий документ перекашивает картину.
    """
    matched = sum(len(s.matched) for s in scores)
    matched_findings = sum(s.matched_findings for s in scores)
    expected = sum(s.expected_total for s in scores)
    found = sum(s.found_total for s in scores)
    # Точность считается по НАХОДКАМ (сколько из них попали в эталон), полнота
    # — по РИСКАМ (сколько из заложенных найдено). Знаменатели разные, и это
    # не опечатка: иначе две верные находки на один пункт роняют precision.
    precision = matched_findings / found if found else 0.0
    recall = matched / expected if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    severity_exact = sum(s.severity_exact for s in scores)
    return {
        "договоров": len(scores),
        "эталонных_рисков": expected,
        "находок_модели": found,
        "совпало": matched,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "критичность_верна_из_совпавших": (f"{severity_exact}/{matched}" if matched else "0/0"),
        "критичность_занижена": sum(len(s.severity_understated) for s in scores),
        "критичность_завышена": sum(len(s.severity_overstated) for s in scores),
        "секунд_всего": round(sum(s.elapsed_sec for s in scores), 1),
    }
