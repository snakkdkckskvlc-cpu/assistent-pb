"""Загрузка текстовых промптов, общая для всех трёх пайплайнов."""

from __future__ import annotations

from .. import config


def load_prompt(name: str) -> str:
    return (config.PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")
