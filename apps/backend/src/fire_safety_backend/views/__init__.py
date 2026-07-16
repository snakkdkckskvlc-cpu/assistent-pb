"""Роутеры backend'а. main.py собирает их через include_router."""
from . import (
    addressees,
    downloads,
    health,
    legal,
    letter,
    spellcheck,
    static_pages,
    tasks,
)

__all__ = [
    "addressees",
    "downloads",
    "health",
    "legal",
    "letter",
    "spellcheck",
    "static_pages",
    "tasks",
]
