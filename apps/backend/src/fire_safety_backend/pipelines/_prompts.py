"""Загрузка текстовых промптов и общие хелперы для всех трёх пайплайнов."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import config

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..infrastructure.queue import Task


NEGATIVE_SUFFIX = "_negative"


def negative_prompt_path(name: str):
    """Файл с негативными примерами, собранными из отзывов 👎.

    Отдельный файл, а не дописывание в конец основного промпта: скрипт
    scripts/update_prompts_from_feedback.py запускается многократно, и правка
    исходного legal.txt накапливала бы примеры без возможности откатиться, а
    заодно ломала бы git-историю самого промпта. Здесь файл переписывается
    целиком, его видно в diff отдельно, и удаление возвращает прежнее
    поведение.
    """
    return config.PROMPTS_DIR / f"{name}{NEGATIVE_SUFFIX}.txt"


def load_prompt(name: str) -> str:
    """Основной промпт плюс негативные примеры из отзывов, если они собраны."""
    text = (config.PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
    negative = negative_prompt_path(name)
    if negative.exists():
        extra = negative.read_text(encoding="utf-8").strip()
        if extra:
            text = f"{text.rstrip()}\n\n{extra}\n"
    return text


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
