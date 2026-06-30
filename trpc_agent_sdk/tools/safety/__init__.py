# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Script Safety Guard.

An *execution-time policy gate plus observability* for tool / skill /
CodeExecutor payloads. It statically scans a script or command before it runs
and returns an ``allow`` / ``deny`` / ``needs_human_review`` decision together
with a structured report and an auditable event.

This is a pre-execution gate in a defence-in-depth chain; it does **not**
replace the runtime isolation a sandbox provides.

Note on imports: the data models, policy, scanners and engine are pure and have
no heavy dependencies. The Filter and wrappers (``filter``/``wrapper`` modules)
import framework pieces (``trpc_agent_sdk.filter``, ``BashTool``,
``BaseCodeExecutor``) and are imported lazily by ``__getattr__`` so that simply
importing this package for the engine does not drag in the full tool stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .audit import AuditLogger
from .audit import build_audit_record
from .audit import emit_safety_span
from .engine import SafetyEngine
from .models import Decision
from .models import Evidence
from .models import Language
from .models import RiskFinding
from .models import RiskLevel
from .models import RiskType
from .models import SafetyReport
from .models import ScanInput
from .models import SuggestedAction
from .policy import PolicyError
from .policy import SafetyPolicy
from .policy import load_policy
from .rules import RULES
from .rules import RuleSpec
from .scanners import BashScanner
from .scanners import PythonScanner

if TYPE_CHECKING:  # pragma: no cover
    from .filter import ToolSafetyFilter
    from .filter import extract_scan_input
    from .wrapper import GuardedCodeExecutor
    from .wrapper import SafeBashTool
    from .wrapper import guard_code_executor

__all__ = [
    # models
    "Decision",
    "Evidence",
    "Language",
    "RiskFinding",
    "RiskLevel",
    "RiskType",
    "SafetyReport",
    "ScanInput",
    "SuggestedAction",
    # policy
    "PolicyError",
    "SafetyPolicy",
    "load_policy",
    # rules
    "RULES",
    "RuleSpec",
    # scanners / engine
    "BashScanner",
    "PythonScanner",
    "SafetyEngine",
    # audit
    "AuditLogger",
    "build_audit_record",
    "emit_safety_span",
    # filter / wrapper (lazy)
    "ToolSafetyFilter",
    "extract_scan_input",
    "SafeBashTool",
    "GuardedCodeExecutor",
    "guard_code_executor",
]

# Lazily expose the framework-coupled pieces so importing the engine alone does
# not require the full tool stack to import successfully.
_LAZY = {
    "ToolSafetyFilter": ".filter",
    "extract_scan_input": ".filter",
    "SafeBashTool": ".wrapper",
    "GuardedCodeExecutor": ".wrapper",
    "guard_code_executor": ".wrapper",
}


def __getattr__(name: str):
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name, __name__)
    return getattr(module, name)
