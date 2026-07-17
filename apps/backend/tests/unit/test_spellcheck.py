"""Юнит-тесты pipelines/spellcheck.py: дедуп LT+LLM, загрузка глоссария.

См. docs/08-references.md и code-review находки №3 (дубли LT+LLM) и
№12 (единый источник глоссария для LT и LLM).
"""

from __future__ import annotations

from fire_safety_backend.pipelines.spellcheck import _dedup_errors, _load_glossary_terms


def test_dedup_drops_llm_duplicate_of_lt_error() -> None:
    # Живой прогон: LT и LLM оба нашли одну и ту же ошибку — раньше
    # показывалась дважды с разными type/reason.
    errors = [
        {
            "type": "стиль",
            "before": "Эта предложение",
            "after": "Это предложение",
            "reason": "согласование рода",
            "source": "languagetool",
            "chunk": 0,
        },
        {
            "type": "грамматика",
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
        {"before": "первая ошибка", "source": "languagetool"},
        {"before": "совсем другая ошибка", "source": "llm"},
    ]
    result = _dedup_errors(errors)
    assert len(result) == 2


def test_dedup_matches_substring_before() -> None:
    # LLM иногда возвращает более короткий фрагмент того же места, что LT.
    errors = [
        {"before": "содержит ошибка в тексте", "source": "languagetool"},
        {"before": "ошибка в тексте", "source": "llm"},
    ]
    result = _dedup_errors(errors)
    assert len(result) == 1
    assert result[0]["source"] == "languagetool"


def test_dedup_ignores_whitespace_and_case_differences() -> None:
    errors = [
        {"before": "Эта  Предложение", "source": "languagetool"},
        {"before": "эта предложение", "source": "llm"},
    ]
    result = _dedup_errors(errors)
    assert len(result) == 1


def test_dedup_multiple_llm_errors_only_removes_conflicting_one() -> None:
    errors = [
        {"before": "ошибка номер один", "source": "languagetool"},
        {"before": "ошибка номер один", "source": "llm"},
        {"before": "совершенно другое место", "source": "llm"},
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
