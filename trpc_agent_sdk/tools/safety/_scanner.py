# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Core scanner for the Tool Script Safety Guard.

Orchestrates pattern rules and AST rules against script text, producing a ScanReport.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import time
from pathlib import Path
from typing import Any
from typing import Optional

from ._policy import SafetyPolicy
from ._rules import BUILTIN_AST_RULES
from ._rules import BUILTIN_PATTERN_RULES
from ._types import Decision
from ._types import RiskLevel
from ._types import RuleFinding
from ._types import ScanReport
from ._types import _DECISION_ORDER
from ._types import _RISK_LEVEL_ORDER

_PYTHON_HEURISTIC = ("\ndef ", "\nimport ", "\nfrom ", "\nclass ", "#!/usr/bin/env python", "#!/usr/bin/python")


class ToolSafetyScanner:
    """Safety scanner for tool-executed scripts and commands.

    Loads a SafetyPolicy and applies pattern and AST rules against
    script text before execution, producing a ScanReport with
    allow/deny/needs_human_review decision.
    """

    def __init__(self, policy_path: Optional[str | Path] = None, policy: Optional[SafetyPolicy] = None):
        if policy is not None:
            self._policy = policy
        elif policy_path is not None:
            self._policy = SafetyPolicy.load(policy_path)
        else:
            raise ValueError("Either policy_path or policy must be provided")

    @property
    def policy(self) -> SafetyPolicy:
        return self._policy

    async def scan(
        self,
        script: str,
        tool_name: str = "unknown",
        args: Optional[dict[str, Any]] = None,
        env_vars: Optional[dict[str, str]] = None,
    ) -> ScanReport:
        start = time.monotonic()

        script_size = len(script.encode("utf-8"))
        if script_size > self._policy.max_script_size_bytes:
            return ScanReport(
                decision=self._policy.default_decision,
                risk_level=RiskLevel.HIGH,
                findings=[],
                scan_duration_ms=(time.monotonic() - start) * 1000,
                script_snippet=script[:200],
                scan_error=f"Script size {script_size} exceeds maximum {self._policy.max_script_size_bytes}",
            )

        if not script.strip():
            return ScanReport(
                decision=Decision.ALLOW,
                scan_duration_ms=(time.monotonic() - start) * 1000,
            )

        try:
            findings = await asyncio.wait_for(
                self._do_scan(script, args, env_vars),
                timeout=self._policy.max_scan_time_ms / 1000,
            )
        except asyncio.TimeoutError:
            return ScanReport(
                decision=self._policy.default_decision,
                risk_level=None,
                findings=[],
                scan_duration_ms=self._policy.max_scan_time_ms,
                script_snippet=script[:200],
                scan_error=f"Scan timed out after {self._policy.max_scan_time_ms}ms",
            )

        findings = self._apply_whitelist_filter(findings, script)
        decision, risk_level = self._resolve_decision(findings)

        return ScanReport(
            decision=decision,
            risk_level=risk_level,
            findings=findings,
            scan_duration_ms=(time.monotonic() - start) * 1000,
            script_snippet=script[:200],
        )

    async def _do_scan(
        self,
        script: str,
        args: Optional[dict[str, Any]],
        env_vars: Optional[dict[str, str]],
    ) -> list[RuleFinding]:
        await asyncio.sleep(0)
        text_to_scan = script
        if args:
            for key, value in args.items():
                if isinstance(value, str):
                    text_to_scan += f"\n{key}={value}"
        if env_vars:
            for key, value in env_vars.items():
                text_to_scan += f"\nexport {key}={value}"

        enabled_rule_ids = {r.rule_id for r in self._policy.get_enabled_rules()}

        findings = []
        for rule in BUILTIN_PATTERN_RULES:
            if rule.rule_id not in enabled_rule_ids:
                continue
            finding = rule.check(text_to_scan)
            if finding:
                findings.append(finding)

        is_python = any(marker in script for marker in _PYTHON_HEURISTIC)
        if is_python:
            try:
                tree = ast.parse(script)
                for rule in BUILTIN_AST_RULES:
                    if rule.rule_id not in enabled_rule_ids:
                        continue
                    findings.extend(rule.check(tree))
            except SyntaxError:
                pass

        return findings

    def _apply_whitelist_filter(self, findings: list[RuleFinding], script: str) -> list[RuleFinding]:
        if not findings:
            return findings

        whitelisted_domains = set(self._policy.whitelist.domains)
        whitelisted_paths = set(self._policy.whitelist.paths)

        if self._is_script_whitelisted(script, whitelisted_domains, whitelisted_paths):
            return []

        filtered = []
        for finding in findings:
            if self._is_whitelisted(finding.evidence, whitelisted_domains, whitelisted_paths):
                continue
            filtered.append(finding)
        return filtered

    def _is_script_whitelisted(
        self, script: str, whitelisted_domains: set[str], whitelisted_paths: set[str]
    ) -> bool:
        for domain in whitelisted_domains:
            if domain in script:
                return True
        for path in whitelisted_paths:
            if path in script:
                return True
        return False

    def _is_whitelisted(
        self, evidence: str, whitelisted_domains: set[str], whitelisted_paths: set[str]
    ) -> bool:
        for domain in whitelisted_domains:
            if domain in evidence:
                return True
        for path in whitelisted_paths:
            if path in evidence:
                return True
        return False

    def _resolve_decision(self, findings: list[RuleFinding]) -> tuple[Decision, Optional[RiskLevel]]:
        if not findings:
            return Decision.ALLOW, None

        policy_rules_by_id = {r.rule_id: r for r in self._policy.get_enabled_rules()}
        for finding in findings:
            if finding.rule_id in policy_rules_by_id:
                finding.risk_level = policy_rules_by_id[finding.rule_id].severity

        risk_level = max(findings, key=lambda f: _RISK_LEVEL_ORDER[f.risk_level]).risk_level

        decisions = []
        for finding in findings:
            if finding.rule_id in policy_rules_by_id:
                decisions.append(policy_rules_by_id[finding.rule_id].effective_decision())
        if not decisions:
            decisions = [self._policy.default_decision]

        decision = max(decisions, key=lambda d: _DECISION_ORDER[d])
        return decision, risk_level

    def hash_script(self, script: str) -> str:
        return hashlib.sha256(script.encode("utf-8")).hexdigest()
