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
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 8.6)
    assert llm._memory_warning(_TAGS) is None


def test_unknown_size_or_ram_does_not_invent_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Не смогли выяснить — молчим: ложная тревога о нехватке памяти хуже,
    чем её отсутствие, потому что заставит менять рабочую конфигурацию."""
    monkeypatch.setattr(config, "LLM_MODEL", "неизвестная:модель")
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 8.6)
    assert llm._memory_warning(_TAGS) is None

    monkeypatch.setattr(config, "LLM_MODEL", "qwen3:30b-a3b")
    monkeypatch.setattr(config, "_total_ram_gb", lambda: 0.0)
    assert llm._memory_warning(_TAGS) is None
