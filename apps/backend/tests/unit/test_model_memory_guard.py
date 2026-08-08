"""Предупреждение, когда выбранная модель не помещается в ОЗУ.

Нехватка памяти проявляется не ошибкой, а бесконечным ожиданием: модель
вытесняется в swap. Замерено на машине разработчика — 8,6 ГБ ОЗУ против
модели на 18,6 ГБ: своп 7,8 ГБ, процессы llama-server вытеснены целиком,
ответ не приходит вообще, а интерфейс показывает «задача выполняется».
"""

from __future__ import annotations

import pytest
from fire_safety_backend import config
from fire_safety_backend.infrastructure import llm

_TAGS = [
    {"name": "qwen2.5:7b-instruct", "size": 4_700_000_000},
    {"name": "qwen3:30b-a3b", "size": 18_600_000_000},
]


def test_oversized_model_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3:30b-a3b")
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 8.6)
    warning = llm._memory_warning(_TAGS)
    assert warning is not None
    assert "18.6" in warning and "8.6" in warning


def test_model_fits_on_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """128 ГБ сервера — та же модель проходит без замечаний."""
    monkeypatch.setattr(config, "LLM_MODEL", "qwen3:30b-a3b")
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 128.0)
    assert llm._memory_warning(_TAGS) is None


def test_small_model_on_small_machine_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "LLM_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setattr(config, "LLM_MODEL_LEGAL", "qwen2.5:7b-instruct")
    monkeypatch.setattr(config, "LLM_MODEL_SPELLCHECK", "qwen2.5:7b-instruct")
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 8.6)
    assert llm._memory_warning(_TAGS) is None


# --- Две модели сразу ---------------------------------------------------------

_TWO_MODEL_TAGS = [
    {"name": "qwen2.5:7b-instruct", "size": 4_700_000_000},
    {"name": "hf.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF:Q4_K_M", "size": 7_000_000_000},
]


def test_two_models_are_counted_together(monkeypatch: pytest.MonkeyPatch) -> None:
    """С разными моделями на пайплайны в памяти висят ОБЕ.

    `LLM_KEEP_ALIVE=-1` держит резидентной каждую модель, к которой обращались.
    По отдельности qwen2.5 (4,7 ГБ) и GigaChat (7 ГБ) на машину с 12 ГБ влезают,
    вместе — нет, и проверка по одной модели пропустила бы ровно этот случай.
    """
    monkeypatch.setattr(config, "LLM_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setattr(
        config, "LLM_MODEL_LEGAL", "hf.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF:Q4_K_M"
    )
    monkeypatch.setattr(config, "LLM_MODEL_SPELLCHECK", "qwen2.5:7b-instruct")
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 12.0)

    warning = llm._memory_warning(_TWO_MODEL_TAGS)
    assert warning is not None
    assert "11.7" in warning, warning
    assert "2 модели" in warning


def test_two_models_fit_on_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """На целевом сервере 128 ГБ — обе помещаются с огромным запасом."""
    monkeypatch.setattr(config, "LLM_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setattr(
        config, "LLM_MODEL_LEGAL", "hf.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF:Q4_K_M"
    )
    monkeypatch.setattr(config, "LLM_MODEL_SPELLCHECK", "qwen2.5:7b-instruct")
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 128.0)
    assert llm._memory_warning(_TWO_MODEL_TAGS) is None


def test_used_models_has_no_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Одна и та же модель на двух пайплайнах не должна считаться дважды."""
    monkeypatch.setattr(config, "LLM_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setattr(config, "LLM_MODEL_LEGAL", "qwen2.5:7b-instruct")
    monkeypatch.setattr(config, "LLM_MODEL_SPELLCHECK", "qwen2.5:7b-instruct")
    assert config.used_models() == ["qwen2.5:7b-instruct"]


def test_legal_model_is_gigachat_by_default() -> None:
    """Замер на датасете: F1 0,677 против 0,533 у qwen2.5 при том же времени."""
    assert "GigaChat" in config.LLM_MODEL_LEGAL
    assert config.LLM_MODEL_SPELLCHECK == config.LLM_MODEL, (
        "орфография остаётся на qwen2.5: там решает чтение промпта, "
        "а выигрыш MoE туда не переносится"
    )


def test_unknown_size_or_ram_does_not_invent_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не смогли выяснить — молчим: ложная тревога о нехватке памяти хуже,
    чем её отсутствие, потому что заставит менять рабочую конфигурацию."""
    monkeypatch.setattr(config, "LLM_MODEL", "неизвестная:модель")
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 8.6)
    assert llm._memory_warning(_TAGS) is None

    monkeypatch.setattr(config, "LLM_MODEL", "qwen3:30b-a3b")
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 0.0)
    assert llm._memory_warning(_TAGS) is None


# --- healthcheck проверяет ВСЕ модели ----------------------------------------


async def test_healthcheck_reports_missing_legal_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Зелёный health при отсутствующей модели юранализа — враньё.

    Пока модель была одна, проверка по LLM_MODEL была верной. С разными
    моделями на пайплайны она стала пропускать отказ: health зелёный, а задача
    падает уже в работе — после того как пользователь отправил договор и
    подождал минуты.
    """

    class _Response:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict:
            return {"models": [{"name": "qwen2.5:7b-instruct", "size": 4_700_000_000}]}

    class _Client:
        async def get(self, url: str, timeout: int = 10) -> _Response:
            return _Response()

    monkeypatch.setattr(config, "LLM_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setattr(
        config, "LLM_MODEL_LEGAL", "hf.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF:Q4_K_M"
    )
    monkeypatch.setattr(config, "LLM_MODEL_SPELLCHECK", "qwen2.5:7b-instruct")
    monkeypatch.setattr(llm, "_get_client", lambda: _Client())

    result = await llm.healthcheck()
    assert result["ok"] is False
    assert "GigaChat" in result["warning"]
    assert "ollama pull" in result["warning"]


async def test_healthcheck_is_green_when_both_models_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Response:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict:
            return {"models": _TWO_MODEL_TAGS}

    class _Client:
        async def get(self, url: str, timeout: int = 10) -> _Response:
            return _Response()

    monkeypatch.setattr(config, "LLM_MODEL", "qwen2.5:7b-instruct")
    monkeypatch.setattr(
        config, "LLM_MODEL_LEGAL", "hf.co/ai-sage/GigaChat3.1-10B-A1.8B-GGUF:Q4_K_M"
    )
    monkeypatch.setattr(config, "LLM_MODEL_SPELLCHECK", "qwen2.5:7b-instruct")
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 128.0)
    monkeypatch.setattr(llm, "_get_client", lambda: _Client())

    result = await llm.healthcheck()
    assert result["ok"] is True
    assert result["warning"] is None
    assert len(result["models"]) == 2
