# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool Script Safety Guard.

A pluggable, three-layer static scanner that vets scripts/commands *before*
they execute and returns a tri-state verdict (``allow`` / ``deny`` /
``needs_human_review``):

- **L1 regex** — declarative rules from a YAML policy (change rules without
  changing code).
- **L2 syntax-aware** — Python ``ast`` and Bash ``shlex`` analysis that defeats
  the obfuscations pure pattern matching misses.
- **L3 decision fusion** — ``deny > review > allow`` so uncertain cases are
  never silently allowed.

Two zero-intrusion integration points reuse the framework's own extension
mechanisms:

- :class:`ToolSafetyGuardFilter` — a ``@register_tool_filter`` filter that
  short-circuits a tool call when a script is unsafe.
- :class:`SafeCodeExecutor` — a :class:`BaseCodeExecutor` decorator that scans
  each code block first.

Every decision is audited (JSONL) and emitted as OpenTelemetry span attributes.
This is a static guard that reduces risk; it does not replace a sandbox.
"""

from ._audit import SafetyAuditLogger
from ._audit import set_span_attributes
from ._bash_scanner import scan_bash
from ._executor_wrapper import SafeCodeExecutor
from ._guard_filter import ToolSafetyGuardFilter
from ._policy import RegexRule
from ._policy import SafetyPolicy
from ._policy import default_policy
from ._policy import load_policy
from ._python_scanner import scan_python
from ._scanner import SafetyScanner
from ._types import RiskCategory
from ._types import RiskLevel
from ._types import RuleHit
from ._types import SafetyDecision
from ._types import ScanInput
from ._types import ScanReport
from ._types import ScriptLanguage

__all__ = [
    # data models
    "SafetyDecision",
    "RiskLevel",
    "RiskCategory",
    "ScriptLanguage",
    "RuleHit",
    "ScanInput",
    "ScanReport",
    # policy
    "SafetyPolicy",
    "RegexRule",
    "load_policy",
    "default_policy",
    # engine
    "SafetyScanner",
    "scan_python",
    "scan_bash",
    # integration
    "ToolSafetyGuardFilter",
    "SafeCodeExecutor",
    # audit / telemetry
    "SafetyAuditLogger",
    "set_span_attributes",
]
