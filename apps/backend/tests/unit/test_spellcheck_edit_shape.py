"""Что корректору ВООБЩЕ разрешено делать с текстом.

Запреты на числа, аббревиатуры и «ё» выросли из конкретных наблюдавшихся
случаев — так дыры закрываются бесконечно, и следующую всё равно не угадать: на
каждом новом документе модель придумывает новое.

Здесь перечислено обратное: что можно. Корректор вправе поставить или снять
запятую либо тире и исправить написание одного слова на пару букв. Всё
остальное — не орфография и не пунктуация, каким бы разумным ни выглядело.

Такое ограничение не зависит от текста, поэтому и работает одинаково на письме
и на договоре. Ради этого свойства оно и написано.
"""

from __future__ import annotations

from fire_safety_backend.pipelines.spellcheck import _atomic_edits, _edit_shape_reason

# --- Что разрешено ---


def test_inserting_a_comma_is_allowed() -> None:
    assert _edit_shape_reason("Уверены что сроки", "Уверены, что сроки") is None


def test_inserting_a_dash_is_allowed() -> None:
    assert _edit_shape_reason("компания надёжный партнёр", "компания — надёжный партнёр") is None


def test_removing_a_wrong_comma_is_allowed() -> None:
    assert _edit_shape_reason("работы, выполнены в срок", "работы выполнены в срок") is None


def test_joining_words_is_allowed() -> None:
    """Слитное/раздельное написание: буквы те же, меняется состав слов."""
    assert _edit_shape_reason("что бы Вы могли", "чтобы Вы могли") is None
    assert _edit_shape_reason("выполнены не своевременно", "выполнены несвоевременно") is None


def test_fixing_one_word_spelling_is_allowed() -> None:
    assert _edit_shape_reason("на обьекте заказчика", "на объекте заказчика") is None
    assert _edit_shape_reason("В течении месяца", "В течение месяца") is None


# --- Что запрещено (всё наблюдалось на настоящем договоре) ---


def test_replacing_dash_with_colon_is_forbidden() -> None:
    reason = _edit_shape_reason("а Заказчик — принять", "а Заказчик: принять")
    assert reason is not None


def test_replacing_comma_with_semicolon_is_forbidden() -> None:
    reason = _edit_shape_reason("доверенности, и ООО", "доверенности; ООО")
    assert reason is not None


def test_dropping_the_final_period_is_forbidden() -> None:
    reason = _edit_shape_reason("контроль толщины покрытия.", "контроль толщины покрытия")
    assert reason is not None


def test_recasing_a_word_alongside_punctuation_is_forbidden() -> None:
    """«заключили настоящий Договор о нижеследующем» → «настоящий договор, о
    нижеследующем»: заодно с запятой переписан определённый термин договора.
    Настоящие ошибки регистра ловит LanguageTool, его находки сюда не идут."""
    reason = _edit_shape_reason(
        "заключили настоящий Договор о нижеследующем",
        "заключили настоящий договор, о нижеследующем",
    )
    assert reason is not None


def test_whitespace_only_change_is_forbidden() -> None:
    """«НДС 20 %» → «НДС 20%» — форматирование, а не орфография."""
    assert _edit_shape_reason("НДС 20 % от суммы", "НДС 20% от суммы") is not None


def test_rewriting_a_word_into_another_is_forbidden() -> None:
    """«не медленно» → «быстро» модель предлагала всерьёз."""
    reason = _edit_shape_reason("выехал не медленно после", "выехал быстро после")
    assert reason is not None


def test_changing_two_words_at_once_is_forbidden() -> None:
    reason = _edit_shape_reason(
        "часть оборудавания не саответствует", "часть оборудования не соответствует"
    )
    assert reason is not None


def test_expanding_a_word_is_forbidden() -> None:
    reason = _edit_shape_reason("соответствия ФЗ от", "соответствия Федерального от")
    assert reason is not None


# --- Разбор правки на отдельные изменения ---


def test_atomic_edits_splits_a_bundled_correction() -> None:
    """Модель кладёт в одну правку верную запятую и подмену слова рядом.
    Целиком такую пару нельзя ни принять, ни отвергнуть без потери."""
    edits = _atomic_edits(
        "Как Вам известно наша организация выполняет работы по своду правил",
        "Как Вам известно, наша организация выполняет работы по СП",
    )
    assert len(edits) == 2
    good = [(b, a) for b, a in edits if _edit_shape_reason(b, a) is None]
    bad = [(b, a) for b, a in edits if _edit_shape_reason(b, a) is not None]
    assert len(good) == 1, "верная запятая обязана уцелеть"
    assert "известно," in good[0][1]
    assert len(bad) == 1, "подмена слов обязана отсеяться"


def test_atomic_edits_keeps_a_word_of_context() -> None:
    """Без контекста правка не находится в документе и не читается человеком."""
    edits = _atomic_edits("Уверены что сроки реальны", "Уверены, что сроки реальны")
    assert edits == [("Уверены что", "Уверены, что")]


def test_atomic_edits_on_a_single_word_fix() -> None:
    assert _atomic_edits("на обьекте работы", "на объекте работы") == [
        ("на обьекте работы", "на объекте работы")
    ]


def test_atomic_edits_splits_adjacent_changes_word_by_word() -> None:
    """difflib склеивает СОСЕДНИЕ изменения в один replace, и верная правка
    уезжала вместе с неверной: точка на двоеточие (нельзя) и запятая при
    причастном обороте (можно) стояли рядом и судились одним куском."""
    edits = _atomic_edits(
        "сообщаем следующее. Договор заключенный сторонами",
        "сообщаем следующее: Договор, заключенный сторонами",
    )
    allowed = [(b, a) for b, a in edits if _edit_shape_reason(b, a) is None]
    rejected = [(b, a) for b, a in edits if _edit_shape_reason(b, a) is not None]
    assert any("Договор," in a for _, a in allowed), "запятая обязана уцелеть"
    assert any(":" in a for _, a in rejected), "двоеточие обязано отсеяться"


def test_atomic_edits_context_never_comes_from_the_changed_part() -> None:
    """Соседнее слово внутри изменённого блока на двух сторонах разное. Взяв
    его в контекст, мы сравнивали бы не то: правка выглядела бы меняющей состав
    слов, даже если она ставит одну запятую."""
    edits = _atomic_edits(
        "сообщаем следующее. Договор заключенный",
        "сообщаем следующее: Договор, заключенный",
    )
    for before, after in edits:
        assert len(before.split()) == len(after.split()), (
            f"контекст разъехался: {before!r} -> {after!r}"
        )


# --- словарь отличает опечатку от грамматики ---


def test_word_the_dictionary_accepts_is_not_a_spelling_fix() -> None:
    """Замерено на четырёх настоящих договорах: модель правила там падежи —
    «и любые» → «и любых», «доступа на» → «доступ на», «45 (сорока пяти)» →
    «45 (сорок пяти)» в прописной сумме. Форма такой правки неотличима от
    исправления опечатки: одно слово, пара букв. Отличает её словарь."""
    known = frozenset({"обьекте"})
    assert _edit_shape_reason("и любые расходы", "и любых расходы", known) is not None
    assert _edit_shape_reason("доступа на объект", "доступ на объект", known) is not None


def test_word_the_dictionary_flags_may_be_fixed() -> None:
    known = frozenset({"обьекте"})
    assert _edit_shape_reason("на обьекте работы", "на объекте работы", known) is None


def test_without_a_dictionary_the_check_does_not_fire() -> None:
    """Пустой словарь означает «сведений нет», а не «всё написано верно»:
    LanguageTool мог быть недоступен, и глушить на этом все правки нельзя."""
    assert _edit_shape_reason("и любые расходы", "и любых расходы") is None
