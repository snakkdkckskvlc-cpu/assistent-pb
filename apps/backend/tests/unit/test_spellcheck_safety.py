"""Запреты на правки, которых корректор делать не вправе.

Проверка орфографии и пунктуации не меняет СОДЕРЖАНИЕ документа. Эти тесты
появились после замера на настоящем договоре — не на размеченных письмах, а на
вычитанном юридическом тексте, который писали не мы. Пайплайн выдал там 14
находок, и почти все от модели оказались выдумкой, включая «2.2. Авансирование»
→ «2,2. Авансирование»: порчу номера пункта договора.

Для инструмента, которым правят договоры с заказчиком, такая правка хуже
пропуска. Пропущенную запятую человек переживёт, испорченный номер пункта —
нет, и заметит его не он, а вторая сторона.
"""

from __future__ import annotations

from fire_safety_backend.pipelines.spellcheck import _keep_applicable, _unsafe_reason

# --- Цифры ---


def test_changing_a_clause_number_is_forbidden() -> None:
    """Наблюдавшийся случай: номер пункта договора превращался в дробь."""
    assert _unsafe_reason("2.2. Авансирование не производится.", "2,2. Авансирование") is not None


def test_changing_a_sum_is_forbidden() -> None:
    assert _unsafe_reason("составляет 9 870 000 рублей", "составляет 9 870 000,00 рублей")


def test_punctuation_fix_next_to_digits_is_allowed() -> None:
    """Запрет на цифры не должен мешать правке пунктуации рядом с ними."""
    assert _unsafe_reason("акта от 12 марта мы приняли", "акта от 12 марта, мы приняли") is None


def test_comma_glued_to_a_number_is_still_allowed() -> None:
    """Запятая, вставленная вплотную к числу, принадлежит предложению, а не
    числу. Наивное сравнение цифр вместе с разделителями отвергало бы её."""
    assert _unsafe_reason("работ 100 однако замечания", "работ 100, однако замечания") is None


# --- Аббревиатуры ---


def test_expanding_an_abbreviation_is_forbidden() -> None:
    """«ФЗ» → «Федерального закона» — это уже не орфография."""
    reason = _unsafe_reason(
        "сертификат соответствия ФЗ от 22.07.2008",
        "сертификат соответствия Федерального закона от 22.07.2008",
    )
    assert reason is not None


def test_abbreviation_kept_in_place_is_allowed() -> None:
    assert _unsafe_reason("требованиям ГОСТ и СП", "требованиям ГОСТ, и СП") is None


def test_one_of_two_identical_abbreviations_replaced_is_forbidden() -> None:
    """Наблюдавшийся случай: «соответствия ФЗ ... № 123-ФЗ» → первое вхождение
    развёрнуто, второе осталось. Проверка по МНОЖЕСТВУ такое пропускала —
    «ФЗ» ведь никуда не делось."""
    reason = _unsafe_reason(
        "соответствия ФЗ от 22.07.2008 № 123-ФЗ",
        "соответствия Федерального закона от 22.07.2008 № 123-ФЗ",
    )
    assert reason is not None


# --- Буква ё ---


def test_removing_yo_is_forbidden() -> None:
    assert _unsafe_reason("на дату зачёта", "на дату зачета") is not None


def test_restoring_yo_is_allowed() -> None:
    """Обратное направление восстанавливает букву и правкой быть вправе."""
    assert _unsafe_reason("на дату зачета", "на дату зачёта") is None


# --- Через весь фильтр ---


def test_unsafe_correction_is_dropped_from_results() -> None:
    text = "2.2. Авансирование не производится."
    errors = [{"before": "2.2. Авансирование", "after": "2,2. Авансирование", "source": "llm"}]
    assert _keep_applicable(errors, text) == []


def test_safe_correction_survives_the_filter() -> None:
    text = "Уверены что сотрудничество продолжится."
    errors = [{"before": "Уверены что", "after": "Уверены, что", "source": "llm"}]
    kept = _keep_applicable(errors, text)
    assert len(kept) == 1


def test_deterministic_sources_are_not_second_guessed() -> None:
    """LanguageTool и домашние правила детерминированы и проверены тестами —
    пропускать их через эвристику незачем."""
    errors = [{"before": "2.2", "after": "2,2", "source": "languagetool"}]
    assert _keep_applicable(errors, "2.2 пункт") == errors
