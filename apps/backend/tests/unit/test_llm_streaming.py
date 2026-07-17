"""Юнит-тесты infrastructure/llm.py::chat (стриминг-режим on_delta) и
pipelines/_prompts.py::make_token_counter.

См. часть 2 плана "фиксы код-ревью + стриминг + фидбек + бенчмарк" —
живой индикатор прогресса (счётчик токенов) вместо полноценного SSE:
все три пайплайна возвращают структурный JSON, частичный JSON нерендерибелен.
"""

from __future__ import annotations

import json

from fire_safety_backend.infrastructure import llm
from fire_safety_backend.infrastructure.queue import Task
from fire_safety_backend.pipelines._prompts import make_token_counter


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeNonStreamClient:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.calls: list[dict] = []

    async def post(self, url: str, json: dict) -> _FakeResponse:
        self.calls.append({"url": url, "json": json})
        return _FakeResponse(self._payload)


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def raise_for_status(self) -> None:
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    async def __aenter__(self) -> _FakeStreamResponse:
        return _FakeStreamResponse(self._lines)

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeStreamClient:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.calls: list[dict] = []

    def stream(self, method: str, url: str, json: dict):
        self.calls.append({"method": method, "url": url, "json": json})
        return _FakeStreamCtx(self._lines)


async def test_chat_non_streaming_returns_content(monkeypatch) -> None:
    client = _FakeNonStreamClient({"message": {"content": "ответ модели"}})
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    result = await llm.chat("system", "user")

    assert result == "ответ модели"
    assert client.calls[0]["json"]["stream"] is False


async def test_chat_streaming_accumulates_content_and_calls_on_delta(monkeypatch) -> None:
    lines = [
        json.dumps({"message": {"content": "Привет"}, "done": False}),
        json.dumps({"message": {"content": ", мир"}, "done": False}),
        json.dumps({"message": {"content": "!"}, "done": True}),
    ]
    client = _FakeStreamClient(lines)
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    deltas: list[str] = []
    result = await llm.chat("system", "user", on_delta=deltas.append)

    assert result == "Привет, мир!"
    assert deltas == ["Привет", ", мир", "!"]
    assert client.calls[0]["json"]["stream"] is True


async def test_chat_streaming_skips_empty_content_chunks(monkeypatch) -> None:
    lines = [
        json.dumps({"message": {"content": "текст"}, "done": False}),
        json.dumps({"message": {}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True}),
    ]
    client = _FakeStreamClient(lines)
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    deltas: list[str] = []
    result = await llm.chat("system", "user", on_delta=deltas.append)

    assert result == "текст"
    assert deltas == ["текст"]


async def test_chat_streaming_skips_blank_lines(monkeypatch) -> None:
    lines = [
        "",
        json.dumps({"message": {"content": "а"}, "done": False}),
        "   ",
        json.dumps({"message": {"content": "б"}, "done": True}),
    ]
    client = _FakeStreamClient(lines)
    monkeypatch.setattr(llm, "_get_client", lambda: client)

    result = await llm.chat("system", "user", on_delta=lambda _d: None)
    assert result == "аб"


def test_make_token_counter_returns_none_without_task() -> None:
    assert make_token_counter(None) is None


def test_make_token_counter_increments_task_tokens() -> None:
    task = Task(id="t1", kind="spellcheck")
    counter = make_token_counter(task)
    assert counter is not None

    counter("a")
    counter("bb")
    counter("ccc")

    assert task.tokens == 3
