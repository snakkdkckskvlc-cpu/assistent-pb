"""Роутеры backend'а. main.py собирает их через include_router."""

from . import (
    addressees,
    batch,
    data,
    downloads,
    feedback,
    health,
    history,
    legal,
    letter,
    spellcheck,
    static_pages,
    tasks,
)

__all__ = [
    "addressees",
    "batch",
    "data",
    "downloads",
    "feedback",
    "health",
    "history",
    "legal",
    "letter",
    "spellcheck",
    "static_pages",
    "tasks",
]
