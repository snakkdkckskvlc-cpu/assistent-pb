"""Домашние правила для частых ошибок делового письма.

Третий проход рядом с LanguageTool и моделью — для класса ошибок, который
перечислим и потому не нуждается в вероятностной модели.

### Зачем, если есть и словарь, и модель

Замерено на размеченном наборе (scripts/evaluate_spellcheck.py): три ошибки
устойчиво не давались ни тому, ни другому. LanguageTool их не знает (проверено
запросом к его API — на этих предложениях он молчит), а семимиллиардная модель
находит их через раз и иногда предлагает неверную правку («Однако, просим» —
запятая не с той стороны союза).

Правило здесь надёжнее модели по природе задачи: «не своевременно» пишется
слитно всегда, кроме противопоставления, и это условие проверяется, а не
угадывается. Стоит правило ноль секунд против минут на модель.

### Честная граница

Правил намеренно мало и они узкие. Каждое покрывает случай, где ошибиться
почти невозможно, и имеет явную оговорку — место, где правило молчит вместо
того, чтобы предложить неверное. Широкие правила («так же» → «также») сюда НЕ
добавлены: «сделай так же, как в прошлый раз» пишется раздельно, и различить
это регулярным выражением нельзя. Такие случаи остаются модели.

Правила не заменяют ни словарь, ни модель — они закрывают ровно то, что те не
закрывают.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Callable

SOURCE = "правило"

# Наречия на -о, которые с «не» пишутся слитно, если нет противопоставления.
# Список закрытый и короткий намеренно: это те, что реально встречаются в
# переписке о сроках и качестве работ. Открытый список («любое наречие на -о»)
# ловил бы «не менее», «не более» и прочие устойчивые сочетания.
_NE_ADVERBS = (
    "своевременно",
    "медленно",
    "качественно",
    "полно",
    "точно",
    "верно",
    "правильно",
    "однократно",
    "аккуратно",
)

# Существительные времени после «в течение». «В течении реки» — единственное
# частое исключение, и оно сюда не попадает именно потому, что список закрытый.
_TIME_NOUNS = (
    "месяц",
    "год",
    "недел",
    "дн",
    "сут",
    "час",
    "лет",
    "квартал",
    "срок",
    "период",
    "врем",
)

# Усилители, после которых «не» с наречием пишется РАЗДЕЛЬНО.
_NE_INTENSIFIERS = ("вовсе", "отнюдь", "далеко", "совсем", "никак")


class Finding(NamedTuple):
    before: str
    after: str
    type: str
    reason: str


class _Rule(NamedTuple):
    name: str
    pattern: re.Pattern[str]
    build: Callable[[re.Match[str], str], Finding | None]


def _ne_adverb(match: re.Match[str], text: str) -> Finding | None:
    adverb = match.group("adv")
    # Противопоставление: «выполнены не своевременно, а с опозданием» —
    # раздельно, и это не ошибка. Правило обязано промолчать.
    tail = text[match.end() : match.end() + 40]
    if re.match(r"\s*,\s*а\b", tail):
        return None
    head = text[max(0, match.start() - 24) : match.start()]
    if any(word in head.lower() for word in _NE_INTENSIFIERS):
        return None
    return Finding(
        before=match.group(0),
        after=f"не{adverb}",
        type="орфография",
        reason=f"Наречие с «не» без противопоставления пишется слитно: «не{adverb}».",
    )


def _v_techenie(match: re.Match[str], text: str) -> Finding | None:
    return Finding(
        before=match.group(0),
        after=f"{match.group('prep')} течение {match.group('rest')}",
        type="орфография",
        reason="Производный предлог времени пишется «в течение».",
    )


def _comma_before_odnako(match: re.Match[str], text: str) -> Finding | None:
    return Finding(
        before=match.group(0),
        after=f"{match.group('prev')}, однако",
        type="пунктуация",
        reason="Перед противительным союзом «однако» ставится запятая.",
    )


_RULES: tuple[_Rule, ...] = (
    _Rule(
        name="не+наречие слитно",
        pattern=re.compile(rf"\bне\s+(?P<adv>{'|'.join(_NE_ADVERBS)})\b", re.IGNORECASE),
        build=_ne_adverb,
    ),
    _Rule(
        name="в течение",
        pattern=re.compile(
            rf"\b(?P<prep>[Вв])\s+течении\s+(?P<rest>(?:\w+\s+){{0,2}}?(?:{'|'.join(_TIME_NOUNS)})\w*)"
        ),
        build=_v_techenie,
    ),
    _Rule(
        name="запятая перед «однако»",
        # Требование словесного символа перед пробелом само отсекает «однако» в
        # начале предложения (там впереди точка) и уже обособленное «, однако».
        pattern=re.compile(r"(?P<prev>\w+)\s+однако\b"),
        build=_comma_before_odnako,
    ),
)


def check(text: str) -> list[dict]:
    """Находки домашних правил в формате пайплайна проверки орфографии.

    Пустой список — правил не сработало. Дубликаты по одному и тому же месту
    не отдаются: одно место — одна правка.
    """
    found: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for rule in _RULES:
        for match in rule.pattern.finditer(text):
            span = (match.start(), match.end())
            if span in seen:
                continue
            finding = rule.build(match, text)
            if finding is None:
                continue
            seen.add(span)
            found.append(
                {
                    "type": finding.type,
                    "before": finding.before,
                    "after": finding.after,
                    "reason": finding.reason,
                    "source": SOURCE,
                    "rule": rule.name,
                }
            )
    return found
