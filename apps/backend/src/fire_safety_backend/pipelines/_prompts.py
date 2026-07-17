"""Загрузка текстовых промптов и общие хелперы для всех трёх пайплайнов."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import config

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..infrastructure.queue import Task


def load_prompt(name: str) -> str:
    return (config.PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def make_token_counter(task: Task | None) -> Callable[[str], None] | None:
    """Колбэк для llm.chat/chat_json(on_delta=...): считает потоковые чанки
    от Ollama (~1 чанк ≈ 1 токен) в task.tokens — растущий индикатор
    прогресса в UI (см. infrastructure/llm.py::chat, app.js::pollTask).
    None, если задачи нет (например, превью без очереди) — тогда llm.chat
    остаётся в обычном нестриминговом режиме."""
    if task is None:
        return None

    def _on_delta(_delta: str) -> None:
        task.tokens += 1

    return _on_delta
