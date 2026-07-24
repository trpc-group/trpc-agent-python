"""Deterministic evaluation + optimization loop example.

This package is intentionally example-local. It mirrors the shape of an
Evaluator/Optimizer workflow while keeping fake model and fake judge execution
offline, deterministic, and easy to inspect.
"""

from .schemas import CandidatePrompt
from .schemas import CaseDelta
from .schemas import CaseResult
from .schemas import EvalCase
from .schemas import EvalResult
from .schemas import GateDecision
from .schemas import OptimizationReport

__all__ = [
    "CandidatePrompt",
    "CaseDelta",
    "CaseResult",
    "EvalCase",
    "EvalResult",
    "GateDecision",
    "OptimizationReport",
]
