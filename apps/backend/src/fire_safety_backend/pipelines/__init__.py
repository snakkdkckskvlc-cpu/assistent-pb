"""Пайплайны функций backend'а."""

from .batch import run_batch
from .legal import run_legal_analysis
from .letter import run_letter
from .spellcheck import run_spellcheck

__all__ = ["run_batch", "run_legal_analysis", "run_letter", "run_spellcheck"]
