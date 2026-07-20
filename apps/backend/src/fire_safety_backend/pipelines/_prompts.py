"""Загрузка текстовых промптов и общие хелперы для всех трёх пайплайнов."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import config

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..infrastructure.queue import Task


def load_prompt(name: str) -> str:
    return (config.PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def make_progress_counter(
    task: Task | None,
    expected_tokens: int,
    base_percent: int = 0,
    span_percent: int = 100,
) -> Callable[[str], None] | None:
    """Колбэк для llm.chat/chat_json(on_delta=...): на каждый потоковый чанк
    от Ollama (~1 чанк ≈ 1 токен) пересчитывает task.percent — полоса
    загрузки в UI (см. infrastructure/llm.py::chat, app.js::pollTask).

    Честная (не фейковая) оценка: доля от ожидаемого числа токенов
    (LLM_NUM_PREDICT_* — потолок ответа для этого вызова), а не таймер и не
    случайное приближение к 100%. base_percent/span_percent — доля общей
    полосы, которую занимает именно этот LLM-вызов, когда в задаче их
    несколько подряд (например, по чанку текста за раз).

    None, если задачи нет (например, превью без очереди) — тогда llm.chat
    остаётся в обычном нестриминговом режиме."""
    if task is None:
        return None

    call_tokens = 0

    def _on_delta(_delta: str) -> None:
        nonlocal call_tokens
        call_tokens += 1
        task.tokens += 1  # кумулятивно на всю задачу — для истории (services/history.py)
        frac = min(1.0, call_tokens / expected_tokens) if expected_tokens > 0 else 0.0
        task.percent = base_percent + int(span_percent * frac)

    return _on_delta
