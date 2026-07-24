# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Scanning orchestration engine for the Tool Script Safety Guard.

This module provides the SafetyScanner class which acts as the unified
entry point for script scanning. It detects the script type, dispatches
to the appropriate language-specific scanner, aggregates results, and
produces a structured SafetyReport.
"""

from __future__ import annotations

import datetime
import time

from trpc_agent_sdk.log import logger

from ._bash_scanner import BashScanner
from ._policy import SafetyPolicy
from ._python_scanner import PythonScanner
from ._types import RiskLevel
from ._types import RuleMatch
from ._types import SafetyDecision
from ._types import SafetyReport
from ._types import ScanInput
from ._types import ScriptType


class SafetyScanner:
    """Unified scanning engine that dispatches to language-specific scanners.

    The SafetyScanner detects the script type from the ScanInput, dispatches
    to the appropriate scanner (BashScanner or PythonScanner), aggregates
    all rule matches, and produces a structured SafetyReport.

    Usage::

        scanner = SafetyScanner(policy)
        report = scanner.scan(scan_input)
        if report.is_blocked:
            # Script was blocked
        elif report.needs_review:
            # Needs human review
        else:
            # Script is safe to execute
    """

    def __init__(self, policy: SafetyPolicy) -> None:
        """Initialize the scanner with a safety policy.

        Args:
            policy: The loaded SafetyPolicy instance.
        """
        self._policy = policy
        self._bash_scanner = BashScanner(policy)
        self._python_scanner = PythonScanner(policy)

    def scan(self, scan_input: ScanInput) -> SafetyReport:
        """Scan a script for security risks.

        This is the main entry point. It detects the script type,
        dispatches to the appropriate scanner, and builds a structured
        SafetyReport with the overall decision.

        Args:
            scan_input: The script content and context to scan.

        Returns:
            A SafetyReport with the scanning results.
        """
        start_time = time.monotonic()

        # Detect script type if not explicitly set
        script_type = scan_input.script_type
        if script_type == ScriptType.UNKNOWN:
            script_type = self._detect_script_type(scan_input.script_content)

        # Dispatch to appropriate scanner
        matches: list[RuleMatch] = []
        if script_type == ScriptType.PYTHON:
            logger.debug("SafetyScanner: Scanning Python script (tool=%s)", scan_input.tool_name)
            matches = self._python_scanner.scan(scan_input)
        elif script_type == ScriptType.BASH:
            logger.debug("SafetyScanner: Scanning Bash script (tool=%s)", scan_input.tool_name)
            matches = self._bash_scanner.scan(scan_input)
        else:
            # Unknown type: try both scanners
            logger.debug("SafetyScanner: Unknown script type, trying both scanners (tool=%s)", scan_input.tool_name)
            matches = self._python_scanner.scan(scan_input)
            if not matches:
                matches = self._bash_scanner.scan(scan_input)

        scan_duration_ms = (time.monotonic() - start_time) * 1000

        # Build report
        report = self._build_report(matches, scan_input, script_type, scan_duration_ms)
        return report

    # ── Report Building ──────────────────────────────────────────────

    def _build_report(
        self,
        matches: list[RuleMatch],
        scan_input: ScanInput,
        script_type: ScriptType,
        scan_duration_ms: float,
    ) -> SafetyReport:
        """Build a SafetyReport from the scan results.

        Args:
            matches: List of rule matches from the scanner.
            scan_input: The original scan input.
            script_type: The detected or specified script type.
            scan_duration_ms: Scan duration in milliseconds.

        Returns:
            A populated SafetyReport.
        """
        # Determine overall risk level
        risk_level = self._determine_risk_level(matches)

        # Determine overall decision
        decision = self._determine_decision(matches, risk_level)

        # Build script summary
        summary = self._build_summary(scan_input.script_content)

        report = SafetyReport(
            decision=decision,
            risk_level=risk_level,
            matches=matches,
            tool_name=scan_input.tool_name,
            script_type=script_type,
            script_summary=summary,
            scan_duration_ms=scan_duration_ms,
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            policy_version=self._policy.policy_version,
        )
        return report

    @staticmethod
    def _determine_risk_level(matches: list[RuleMatch]) -> RiskLevel:
        """Determine the highest risk level among all matches.

        Args:
            matches: List of rule matches.

        Returns:
            The highest RiskLevel found, or LOW if no matches.
        """
        if not matches:
            return RiskLevel.LOW
        return max(m.risk_level for m in matches)

    def _determine_decision(
        self,
        matches: list[RuleMatch],
        risk_level: RiskLevel,
    ) -> SafetyDecision:
        """Determine the overall safety decision.

        Rules:
        - CRITICAL matches → DENY
        - HIGH matches → DENY
        - MEDIUM matches → NEEDS_HUMAN_REVIEW
        - No matches or LOW only → ALLOW
        - If no matches at all, use the policy's default_decision.

        Args:
            matches: List of rule matches.
            risk_level: The determined highest risk level.

        Returns:
            The overall SafetyDecision.
        """
        if not matches:
            return self._policy.default_decision

        if risk_level >= RiskLevel.HIGH:
            return SafetyDecision.DENY
        elif risk_level == RiskLevel.MEDIUM:
            return SafetyDecision.NEEDS_HUMAN_REVIEW
        else:
            return SafetyDecision.ALLOW

    @staticmethod
    def _build_summary(script_content: str) -> str:
        """Build a truncated and sanitized summary of the script content.

        Args:
            script_content: The full script content.

        Returns:
            A truncated preview (first 200 chars), with newlines escaped.
        """
        # Remove consecutive whitespace for a compact preview
        preview = " ".join(script_content.split())
        if len(preview) > 200:
            preview = preview[:200] + "..."
        return preview

    @staticmethod
    def _detect_script_type(content: str) -> ScriptType:
        """Detect the script type from content heuristics.

        Args:
            content: The script content to analyze.

        Returns:
            The detected ScriptType, or UNKNOWN if uncertain.
        """
        first_line = content.strip().split("\n")[0] if content.strip() else ""

        # Shebang detection
        if first_line.startswith("#!/bin/bash") or first_line.startswith("#!/bin/sh") or \
           first_line.startswith("#!/usr/bin/bash") or first_line.startswith("#!/usr/bin/env bash"):
            return ScriptType.BASH
        if first_line.startswith("#!/usr/bin/env python") or first_line.startswith("#!/usr/bin/python") or \
           first_line.startswith("#!/usr/bin/env python3") or first_line.startswith("#!/usr/bin/python3"):
            return ScriptType.PYTHON

        # Python-specific patterns
        python_patterns = ["import ", "from ", "class ", "def ", "if __name__", "print("]
        bash_patterns = [
            "#!/",
            "echo ",
            "export ",
            "source ",
            "function ",
            "alias ",
            "if [",
            "while ",
            "for ",
            "do ",
            "done",
            "fi",
            "elif",
        ]

        py_score = sum(1 for p in python_patterns if p in content)
        bash_score = sum(1 for p in bash_patterns if p in content)

        if py_score > bash_score:
            return ScriptType.PYTHON
        elif bash_score > py_score:
            return ScriptType.BASH
        return ScriptType.UNKNOWN
