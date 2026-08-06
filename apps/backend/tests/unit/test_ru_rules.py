"""Домашние правила: ловят ли они то, ради чего написаны, и молчат ли там,
где правка была бы неверной.

Каждое правило здесь узкое намеренно. Проверяется не только срабатывание, но и
ОТКАЗ срабатывать — правило, которое не умеет промолчать, хуже отсутствующего:
оно портит правильный текст, и человек перестаёт доверять всей проверке.
"""

from __future__ import annotations

from fire_safety_backend.infrastructure import ru_rules


def _find(text: str) -> list[dict]:
    return ru_rules.check(text)


def _afters(text: str) -> list[str]:
    return [e["after"] for e in _find(text)]


# --- НЕ с наречием ---


def test_ne_adverb_is_joined() -> None:
    got = _find("Отдельные работы были выполнены не своевременно.")
    assert len(got) == 1
    assert got[0]["before"] == "не своевременно"
    assert got[0]["after"] == "несвоевременно"
    assert got[0]["type"] == "орфография"


def test_ne_adverb_stays_apart_when_contrasted() -> None:
    """«не своевременно, а с опозданием» — раздельно, и это НЕ ошибка."""
    assert _find("Работы выполнены не своевременно, а с опозданием.") == []


def test_ne_adverb_stays_apart_after_intensifier() -> None:
    """«вовсе не медленно» — раздельно."""
    assert _find("Специалист выехал вовсе не медленно.") == []


def test_ne_adverb_ignores_words_outside_the_list() -> None:
    """Список закрытый: открытое «любое наречие на -о» ловило бы «не менее»."""
    assert _find("Комиссия работала не менее трёх часов.") == []


# --- в течение ---


def test_v_techenie_before_time_noun() -> None:
    assert _afters("В течении месяца устраним замечания.") == ["В течение месяца"]


def test_v_techenie_with_numeral_between() -> None:
    """Именно этот случай пропускает и LanguageTool, и модель."""
    assert _afters("Работы завершим в течении двух недель.") == ["в течение двух недель"]


def test_v_techenii_reki_is_left_alone() -> None:
    """«в течении реки» — предложный падеж существительного, не предлог."""
    assert _find("Изменения в течении реки зафиксированы.") == []


# --- запятая перед «однако» ---


def test_comma_inserted_before_odnako() -> None:
    got = _find("Работы выполнены качественно однако замечания остаются.")
    assert len(got) == 1
    assert got[0]["after"] == "качественно, однако"
    assert got[0]["type"] == "пунктуация"


def test_odnako_at_sentence_start_is_left_alone() -> None:
    """В начале предложения запятая перед союзом не нужна — и её некуда
    ставить. Правило молчит благодаря требованию слова перед пробелом."""
    assert _find("Замечания устранены. Однако акт пока не подписан.") == []


def test_already_separated_odnako_is_left_alone() -> None:
    assert _find("Мы, однако, считаем сроки реальными.") == []


# --- общее ---


def test_clean_business_text_produces_nothing() -> None:
    """Ложное срабатывание на грамотном тексте дороже пропуска: человек
    перестаёт доверять всей проверке."""
    clean = (
        "Настоящее письмо составлено в двух экземплярах, имеющих равную силу. "
        "Заказчик вправе привлечь независимого эксперта за свой счёт. "
        "Работы выполняются в соответствии с требованиями свода правил."
    )
    assert _find(clean) == []


def test_findings_are_marked_as_rule_source() -> None:
    """Источник виден в интерфейсе и участвует в дедупликации."""
    got = _find("Работы выполнены не своевременно.")
    assert got[0]["source"] == ru_rules.SOURCE


# --- причастный оборот ---


def _participle(text: str) -> list[dict]:
    return [e for e in _find(text) if e["rule"] == "причастный оборот"]


def test_participle_clause_gets_both_commas() -> None:
    """Обе запятые или ни одной. Открывающая без закрывающей — это новая
    ошибка, внесённая инструментом, а не исправление."""
    got = _participle("Договор заключенный сторонами предусматривает гарантийный срок.")
    assert len(got) == 1
    assert got[0]["before"] == "Договор заключенный сторонами"
    assert got[0]["after"] == "Договор, заключенный сторонами,"


def test_participle_clause_spans_several_words() -> None:
    got = _participle("Оборудование установленное на первом этаже прошло испытания.")
    assert got[0]["after"] == "Оборудование, установленное на первом этаже,"


def test_participle_before_the_noun_needs_no_commas() -> None:
    """Обратный порядок: «Смонтированная система прошла» — обособлять нечего."""
    assert _participle("Смонтированная система оповещения не прошла испытания.") == []


def test_short_participle_is_a_predicate_not_a_clause() -> None:
    """«письмо составлено» — сказуемое, а не оборот."""
    assert _participle("Настоящее письмо составлено в двух экземплярах.") == []


def test_already_separated_clause_is_left_alone() -> None:
    assert _participle("Оборудование, поставленное субподрядчиком, было установлено.") == []


def test_adjective_pair_is_not_mistaken_for_a_clause() -> None:
    """Без отсева по окончаниям правило давало 22 ложных срабатывания на
    1,3 млн символов нормативки: «Опасные производственные», «Предельно
    допустимое». Первое слово оборота обязано быть существительным."""
    assert _participle("Опасные производственные объекты подлежат учёту.") == []
    assert _participle("Предельно допустимое значение установлено нормативом.") == []


def test_rule_stays_silent_when_the_clause_end_is_not_found() -> None:
    """Не нашли сказуемое — молчим. Лучше пропустить, чем поставить одну
    запятую из двух."""
    assert _participle("Договор заключенный сторонами") == []


# --- деепричастный оборот ---


def _gerund(text: str) -> list[dict]:
    return [e for e in _find(text) if e["rule"] == "деепричастный оборот"]


def test_gerund_clause_is_separated() -> None:
    """Деепричастный оборот обособляется ВСЕГДА, без исключений вроде тех, что
    есть у причастного, — поэтому правило здесь надёжнее прочих."""
    got = _gerund("Рассмотрев Ваше обращение от 12 марта мы приняли решение.")
    assert len(got) == 1
    assert got[0]["after"] == "Рассмотрев Ваше обращение от 12 марта,"


def test_gerund_clause_of_two_words() -> None:
    assert _gerund("Учитывая сложность объекта мы предлагаем перенос.")[0]["after"] == (
        "Учитывая сложность объекта,"
    )


def test_gerund_with_noun_subject_is_left_alone() -> None:
    """Главная оговорка. Подлежащее-существительное без морфологии не опознать,
    и правило ставило запятую ПОСЛЕ него: «Руководствуясь пунктом 5 договора
    подрядчик, приостановил работы». Поймано на живом примере. Неверно
    поставленная запятая хуже ненайденной, поэтому здесь правило молчит."""
    assert _gerund("Руководствуясь пунктом 5 договора подрядчик приостановил работы.") == []


def test_already_separated_gerund_is_left_alone() -> None:
    assert _gerund("Рассмотрев Ваше обращение, мы приняли решение.") == []


def test_word_outside_the_gerund_list_is_ignored() -> None:
    """Список закрытый: открытое «слово на -в или -я» ловило бы «время» и
    «статья»."""
    assert _gerund("Время выполнения работ мы согласуем отдельно.") == []


# --- обращение, вводный оборот, «что» после глагола речи ---


def _by_rule(text: str, name: str) -> list[dict]:
    return [e for e in _find(text) if e["rule"] == name]


def test_comma_after_the_address() -> None:
    got = _by_rule(
        "Уважаемый Иван Иванович просим Вас рассмотреть заявку.", "запятая после обращения"
    )
    assert len(got) == 1
    assert got[0]["after"] == "Уважаемый Иван Иванович,"


def test_address_already_separated_is_left_alone() -> None:
    assert _by_rule("Уважаемый Иван Иванович, просим Вас.", "запятая после обращения") == []


def test_address_boundary_is_the_switch_to_lowercase() -> None:
    """Границей обращения считается переход от заглавных к строчной: имя и
    отчество пишутся с заглавной, тело предложения — нет."""
    got = _by_rule("Уважаемая Мария Петровна просим согласовать дату.", "запятая после обращения")
    assert got[0]["after"] == "Уважаемая Мария Петровна,"


def test_intro_phrase_gets_a_comma() -> None:
    assert _by_rule("К сожалению сроки поставки сдвинуты.", "вводный оборот")[0]["after"] == (
        "К сожалению,"
    )


def test_intro_phrase_already_separated_is_left_alone() -> None:
    assert _by_rule("К сожалению, сроки сдвинуты.", "вводный оборот") == []


def test_ambiguous_intro_phrase_is_not_in_the_list() -> None:
    """«Таким образом» бывает и обстоятельством («таким образом мы получили
    результат»), и различить это правилом нельзя — поэтому его в списке нет."""
    assert _by_rule("Таким образом работы завершены.", "вводный оборот") == []


def test_comma_before_chto_after_a_speech_verb() -> None:
    got = _by_rule("Уверены что сотрудничество продолжится.", "запятая перед «что»")
    assert got[0]["after"] == "Уверены, что"


def test_comma_before_chto_already_there() -> None:
    assert _by_rule("Уверены, что сотрудничество продолжится.", "запятая перед «что»") == []
