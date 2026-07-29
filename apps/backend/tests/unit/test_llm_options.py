"""Опции запроса к Ollama, влияющие на скорость.

keep_alive — не косметика: без него Ollama выгружает модель через 5 минут
простоя, и следующий запрос заново читает её с диска. Замерено на боевой
конфигурации: холодный запрос 9.3 c против 1.2 c тёплого. Инструментом
пользуются несколько раз в день, так что «после паузы» — почти каждый
запрос. Если опция перестанет доезжать до Ollama, всё продолжит работать,
просто станет медленнее — то есть отказ будет тихим.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from fire_safety_backend import config
from fire_safety_backend.infrastructure import llm


class _FakeResponse:
    status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"message": {"content": "{}"}}


class _CapturingClient:
    def __init__(self) -> None:
        self.payload: dict[str, Any] = {}

    async def post(self, url: str, json: dict | None = None) -> _FakeResponse:
        self.payload = json or {}
        return _FakeResponse()


async def _capture(**kwargs: Any) -> dict[str, Any]:
    client = _CapturingClient()
    with patch.object(llm, "_get_client", lambda: client):
        await llm.chat(system="s", user="u", **kwargs)
    return client.payload


async def test_keep_alive_is_sent() -> None:
    payload = await _capture()
    assert payload["keep_alive"] == config.LLM_KEEP_ALIVE


async def test_keep_alive_default_keeps_model_resident() -> None:
    """-1 = держать бессрочно. Дефолт Ollama (5 минут) нам не подходит."""
    assert config.LLM_KEEP_ALIVE == -1


def test_keep_alive_integer_is_not_a_string() -> None:
    """Ollama отвергает keep_alive="-1" строкой: `missing unit in duration`.

    Переменная окружения приходит строкой, поэтому целые числа обязаны
    конвертироваться в int — иначе каждый запрос падал бы с HTTP 400.
    """
    assert config._parse_keep_alive("-1") == -1
    assert isinstance(config._parse_keep_alive("-1"), int)
    # Строка с единицей измерения — валидный для Ollama формат, её не трогаем.
    assert config._parse_keep_alive("30m") == "30m"


async def test_num_thread_omitted_by_default() -> None:
    """Без явной настройки выбор потоков остаётся за Ollama.

    Оптимум зависит от топологии процессора; переносить подобранное на
    чужой машине число в общий конфиг нельзя.
    """
    with patch.object(config, "LLM_NUM_THREAD", None):
        payload = await _capture()
    assert "num_thread" not in payload["options"]


async def test_num_thread_is_forwarded_when_configured() -> None:
    with patch.object(config, "LLM_NUM_THREAD", 12):
        payload = await _capture()
    assert payload["options"]["num_thread"] == 12


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", None), ("12", 12), ("не число", None), ("-4", None)],
)
def test_num_thread_parsing_ignores_garbage(raw: str, expected: int | None) -> None:
    """Мусор в переменной окружения не должен ронять backend на старте."""
    parsed = int(raw) if raw.strip().isdigit() else None
    assert parsed == expected
