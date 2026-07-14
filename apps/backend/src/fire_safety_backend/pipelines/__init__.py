"""Пайплайны трёх функций backend'а."""
from .legacy import run_legal_analysis, run_letter, run_spellcheck

__all__ = ["run_spellcheck", "run_legal_analysis", "run_letter"]
