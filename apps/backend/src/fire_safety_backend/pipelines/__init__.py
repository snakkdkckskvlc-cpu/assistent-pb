"""Пайплайны трёх функций backend'а."""

from .legal import run_legal_analysis
from .letter import run_letter
from .spellcheck import run_spellcheck

__all__ = ["run_spellcheck", "run_legal_analysis", "run_letter"]
