"""Юнит-тесты pipelines/spellcheck.py: дедуп LT+LLM, загрузка глоссария.

См. docs/08-references.md и code-review находки №3 (дубли LT+LLM) и
№12 (единый источник глоссария для LT и LLM).
"""

from __future__ import annotations

from fire_safety_backend.pipelines.spellcheck import (
    _anchor_to_source,
    _dedup_errors,
    _keep_applicable,
    _load_glossary_terms,
)

_TEXT = "Наша компания надёжный партнёр и дорожит своей репутацией."


def test_anchor_returns_quote_as_is_when_it_matches() -> None:
    assert _anchor_to_source("надёжный партнёр", _TEXT) == "надёжный партнёр"


def test_anchor_finds_fragment_quoted_with_correction_already_applied() -> None:
    """Модель предсказуемо цитирует фрагмент УЖЕ С ИСПРАВЛЕНИЕМ. Замерено:
    тире между подлежащим и сказуемым она находила верно, но присылала «Наша
    компания — надёжный партнёр», хотя в документе тире нет. Правка молча не
    применялась: в списке она есть, в документе её нет."""
    got = _anchor_to_source("Наша компания — надёжный партнёр", _TEXT)
    assert got == "Наша компания надёжный партнёр"


def test_anchor_rejects_text_absent_from_document() -> None:
    assert _anchor_to_source("Согласно своду правил пожарной безопасности", _TEXT) is None


def test_anchor_refuses_dangerously_short_quote_even_if_present() -> None:
    """Правки применяются ГЛОБАЛЬНОЙ заменой (_apply_to_text). Цитата «и»
    дословно есть в тексте, и принять её значило бы переписать каждое «и» в
    документе. Длина проверяется раньше поиска именно поэтому."""
    assert "и" in _TEXT
    assert _anchor_to_source("и", _TEXT) is None


def test_anchor_accepts_single_word_typo() -> None:
    """Порог по длине не должен рубить обычную однословную опечатку."""
    assert _anchor_to_source("репутацией", _TEXT) == "репутацией"


def test_keep_applicable_drops_no_op_correction() -> None:
    """«Исправление», где before совпадает с after, ничего не меняет в
    документе, но в списке выглядит выполненной работой."""
    errors = [
        {
            "before": "Уважаемый Иван Иванович,",
            "after": "Уважаемый Иван Иванович,",
            "source": "llm",
        }
    ]
    assert _keep_applicable(errors, "Уважаемый Иван Иванович, просим Вас.") == []


def test_keep_applicable_rewrites_quote_to_the_document_wording() -> None:
    errors = [
        {"before": "Наша компания — надёжный партнёр", "after": "Наша компания — надёжный партнёр"}
    ]
    kept = _keep_applicable([{**errors[0], "source": "llm"}], _TEXT)
    assert len(kept) == 1
    # Цитата и привязана к тексту документа (тире в исходнике нет), и сужена до
    # места правки — длинный кусок в таблице человеку читать незачем.
    assert kept[0]["before"] in _TEXT
    assert "—" not in kept[0]["before"]
    assert "—" in kept[0]["after"]


def test_keep_applicable_never_touches_languagetool_findings() -> None:
    """LanguageTool отдаёт смещения по исходному тексту — его цитаты дословны
    по построению, и перепривязывать их незачем."""
    errors = [{"before": "чего-то нет в тексте", "after": "исправлено", "source": "languagetool"}]
    assert _keep_applicable(errors, _TEXT) == errors


def test_dedup_drops_llm_duplicate_of_lt_error() -> None:
    # Живой прогон: LT и LLM оба нашли одну и ту же ошибку — раньше
    # показывалась дважды с разными type/reason.
    errors = [
        {
            "type": "орфография",
            "before": "Эта предложение",
            "after": "Это предложение",
            "reason": "согласование рода",
            "source": "languagetool",
            "chunk": 0,
        },
        {
            "type": "орфография",
            "before": "эта предложение",
            "after": "это предложение",
            "reason": "неверный род",
            "source": "llm",
            "chunk": 1,
        },
    ]
    result = _dedup_errors(errors)
    assert len(result) == 1
    assert result[0]["source"] == "languagetool"


def test_dedup_keeps_distinct_errors() -> None:
    errors = [
        {"before": "первая ашибка", "after": "первая ошибка", "source": "languagetool"},
        {"before": "савсем другое", "after": "совсем другое", "source": "llm"},
    ]
    result = _dedup_errors(errors)
    assert len(result) == 2


def test_dedup_drops_llm_finding_with_shorter_quote_of_same_edit() -> None:
    """LLM иногда цитирует более короткий фрагмент того же места, что LT.
    Правка при этом одна и та же — показывать её дважды незачем."""
    errors = [
        {
            "before": "документ содержит ашибку",
            "after": "документ содержит ошибку",
            "source": "languagetool",
        },
        {"before": "содержит ашибку", "after": "содержит ошибку", "source": "llm"},
    ]
    result = _dedup_errors(errors)
    assert len(result) == 1
    assert result[0]["source"] == "languagetool"


def test_dedup_ignores_whitespace_and_case_differences() -> None:
    errors = [
        {"before": "Эта  Предложение", "after": "Это  Предложение", "source": "languagetool"},
        {"before": "эта предложение", "after": "это предложение", "source": "llm"},
    ]
    result = _dedup_errors(errors)
    assert len(result) == 1


def test_dedup_keeps_llm_finding_that_corrects_more_than_lt() -> None:
    """Главный случай, ради которого правило переписано.

    Модель цитирует ЦЕЛОЕ предложение и правит в нём и опечатку, и обособление
    причастного оборота. LanguageTool нашёл в том же предложении только
    опечатку. Раньше находка модели отбрасывалась целиком — просто потому, что
    цитата LT лежала внутри её цитаты, — и обособление пропадало молча.
    Замерено: в письме 01 так исчезали три верные правки подряд.
    """
    errors = [
        {"before": "было устоновлено", "after": "было установлено", "source": "languagetool"},
        {
            "before": "Оборудование поставленное субподрядчиком было устоновлено",
            "after": "Оборудование, поставленное субподрядчиком, было установлено",
            "source": "llm",
        },
    ]
    result = _dedup_errors(errors)
    assert len(result) == 2, "правка модели содержала обособление сверх находки LT"


def test_dedup_drops_llm_finding_that_corrects_subset_of_lt() -> None:
    """Обратное направление: модель исправила меньше, чем LT, в том же месте."""
    errors = [
        {
            "before": "В течении месяца обязуеться",
            "after": "В течение месяца обязуется",
            "source": "languagetool",
        },
        {"before": "обязуеться", "after": "обязуется", "source": "llm"},
    ]
    result = _dedup_errors(errors)
    assert len(result) == 1
    assert result[0]["source"] == "languagetool"


def test_dedup_multiple_llm_errors_only_removes_conflicting_one() -> None:
    errors = [
        {"before": "ашибка номер один", "after": "ошибка номер один", "source": "languagetool"},
        {"before": "ашибка номер один", "after": "ошибка номер один", "source": "llm"},
        {"before": "савершенно другое", "after": "совершенно другое", "source": "llm"},
    ]
    result = _dedup_errors(errors)
    assert len(result) == 2
    assert {e["source"] for e in result} == {"languagetool", "llm"}


def test_dedup_empty_list() -> None:
    assert _dedup_errors([]) == []


def test_load_glossary_terms_reads_real_dict_file() -> None:
    # Читает реальный tools/languagetool/dict/spelling_global.txt — тот же
    # файл, что подключён к LanguageTool через classpath (единый источник).
    terms = _load_glossary_terms()
    assert "АПС" in terms
    assert "ПожСервис" in terms
    assert all(not t.startswith("#") for t in terms)
