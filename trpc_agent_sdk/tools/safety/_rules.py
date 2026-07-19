# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rule definitions and registry for the Tool Script Safety Guard.

Each rule is a small, focused detector that inspects a script and returns
zero or more :class:`~trpc_agent_sdk.tools.safety._models.Finding` objects.
Rules are *pluggable*: you can register custom rules at runtime via
:class:`RuleRegistry.register`.
"""

from __future__ import annotations

import re
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Optional

from ._models import Decision
from ._models import Finding
from ._models import RiskCategory
from ._models import RiskLevel
from ._models import ScriptType
from ._policy import SafetyPolicy


@dataclass
class ScanContext:
    """Everything a rule needs to inspect a script.

    Attributes:
        script: Raw script content.
        script_type: Detected script type (python / bash).
        args: Command-line arguments that would be passed to the tool.
        cwd: Working directory for execution.
        env: Environment variables (values are *not* scanned for secrets,
            only keys, to avoid log pollution).
        tool_name: Name of the tool that will execute the script.
        policy: The active safety policy.
        cached_tree: Parsed AST (``ast.Module``) for Python scripts,
            parsed once and shared across all rules.  ``None`` for Bash
            or when the script has a syntax error.
        cached_lines: Pre-split lines of *script*, shared across rules
            to avoid repeated ``str.splitlines()`` calls.
        compiled_secrets: Pre-compiled secret-detection regex patterns,
            compiled once and shared across Python/Bash secret rules.
    """

    script: str
    script_type: ScriptType
    args: dict[str, Any]
    cwd: str
    env: dict[str, str]
    tool_name: str
    policy: SafetyPolicy
    cached_tree: Optional[Any] = None
    cached_lines: Optional[list[str]] = None
    compiled_secrets: Optional[list[re.Pattern]] = field(default=None, repr=False)


class Rule(ABC):
    """Abstract base class for all safety rules.

    Subclasses must set the class attributes and implement :meth:`check`.
    """

    #: Unique, stable identifier (e.g. ``"PY-DANGEROUS-RM-RF"``).
    rule_id: str = ""

    #: Human-readable description shown in reports.
    description: str = ""

    #: Risk category this rule belongs to.
    category: RiskCategory = RiskCategory.PROCESS_SYSTEM

    #: Default risk level when the rule triggers (policy may override).
    default_risk_level: RiskLevel = RiskLevel.MEDIUM

    #: Default decision when the rule triggers (policy may override).
    default_decision: Decision = Decision.NEEDS_HUMAN_REVIEW

    #: Which script types this rule applies to.
    applies_to: tuple[ScriptType, ...] = (ScriptType.PYTHON, ScriptType.BASH)

    @abstractmethod
    def check(self, ctx: ScanContext) -> list[Finding]:
        """Inspect *ctx* and return a list of findings (may be empty)."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Helpers used by concrete rules
    # ------------------------------------------------------------------

    def _make_finding(
        self,
        evidence: str,
        line_number: Optional[int] = None,
        recommendation: str = "",
        *,
        risk_level: Optional[RiskLevel] = None,
        decision: Optional[Decision] = None,
    ) -> Finding:
        """Build a :class:`Finding` honouring policy overrides."""
        override = self.ctx.policy.get_rule_override(self.rule_id) if hasattr(self, "ctx") else None
        if override is None:
            override = self._override  # type: ignore[attr-defined]

        effective_risk = risk_level or override.risk_level or self.default_risk_level
        effective_decision = decision or override.decision or self.default_decision

        return Finding(
            rule_id=self.rule_id,
            category=self.category.value,
            risk_level=effective_risk,
            decision=effective_decision,
            description=self.description,
            evidence=evidence,
            line_number=line_number,
            recommendation=recommendation,
        )

    def _resolve_overrides(self, policy: SafetyPolicy) -> None:
        """Cache the policy override for this rule (called by the scanner)."""
        self._override = policy.get_rule_override(self.rule_id)  # type: ignore[attr-defined]
        self.ctx = ScanContext  # type: ignore[assignment]  # placeholder, set per-scan

    @property
    def is_enabled(self) -> bool:
        """Whether this rule is enabled (checked against the cached override)."""
        try:
            return self._override.enabled  # type: ignore[attr-defined]
        except AttributeError:
            return True


class RuleRegistry:
    """Registry of available safety rules.

    Rules are keyed by ``rule_id``.  The registry is a singleton-like
    container so that registering a rule makes it available to all
    :class:`~trpc_agent_sdk.tools.safety._safety_guard.SafetyGuard` instances.
    """

    def __init__(self) -> None:
        self._rules: dict[str, Rule] = {}

    def register(self, rule: Rule) -> Rule:
        """Register *rule*.  Returns *rule* for chaining."""
        if not rule.rule_id:
            raise ValueError("Rule must have a non-empty rule_id")
        self._rules[rule.rule_id] = rule
        return rule

    def unregister(self, rule_id: str) -> None:
        """Remove a rule by id."""
        self._rules.pop(rule_id, None)

    def get(self, rule_id: str) -> Optional[Rule]:
        """Look up a rule by id."""
        return self._rules.get(rule_id)

    def all_rules(self) -> list[Rule]:
        """Return all registered rules (insertion order)."""
        return list(self._rules.values())

    def rules_for(self, script_type: ScriptType) -> list[Rule]:
        """Return rules that apply to *script_type* and are enabled."""
        return [r for r in self._rules.values() if script_type in r.applies_to]

    def clear(self) -> None:
        """Remove all registered rules."""
        self._rules.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: Global rule registry.  Import this to register or inspect rules.
global_rule_registry = RuleRegistry()
