"""Роутеры backend'а. main.py собирает их через include_router."""

from . import (
    addressees,
    auth,
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
    transport,
)

__all__ = [
    "addressees",
    "auth",
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
    "transport",
]
