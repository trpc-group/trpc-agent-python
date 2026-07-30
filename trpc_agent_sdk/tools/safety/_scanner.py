# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Scan orchestrator: the three-layer funnel that produces a :class:`ScanReport`.

``SafetyScanner`` wires the layers together:

- **L1 regex** — the declarative rules from the policy run line-by-line. This is
  the millisecond-fast path that keeps a 500-line script under the 1s budget.
- **L2 syntax-aware** — :func:`scan_python` / :func:`scan_bash` reason about
  structure to defeat the obfuscations that break pure pattern matching.
- **L3 decision fusion** — findings are aggregated with a ``deny > review >
  allow`` ordering (borrowed from Claude Code): any critical/high forces a
  deny, any medium forces human review, and nothing uncertain is silently
  allowed.

Network findings receive a domain-aware second pass (:meth:`_refine_network`):
egress to an allow-listed domain is dropped, egress to a non-listed domain is
kept as high, and egress whose destination cannot be determined statically is
downgraded to *review* rather than blindly blocked or allowed.
"""

from __future__ import annotations

import ast
import re
import time
from typing import Optional

from ._bash_scanner import scan_bash
from ._policy import SafetyPolicy
from ._policy import default_policy
from ._python_scanner import scan_python
from ._types import RiskCategory
from ._types import RiskLevel
from ._types import RuleHit
from ._types import SafetyDecision
from ._types import ScanInput
from ._types import ScanReport
from ._types import ScriptLanguage

# Network hits that are language-agnostic and therefore worth a domain-aware
# refinement pass (the bash L2 layer already refines its own SH02x hits).
_REFINABLE_NETWORK_RULES = {"NET001", "AST004"}

# Heuristics used only when the caller does not declare a language.
_BASH_HINTS = re.compile(
    r"(^\s*#!.*\b(bash|sh|zsh)\b)|(\|\s*\w)|(&&)|(\|\|)|(>\s*/)|(\$\()"
    r"|(\b(sudo|rm|curl|wget|apt|apt-get|yum|chmod|chown|nc|ncat)\b)",
    re.IGNORECASE,
)

_URL_RE = re.compile(r"https?://([^/\s'\"]+)", re.IGNORECASE)

# Secret-looking substrings masked when redaction is enabled.
_SECRET_PATTERNS = [
    re.compile(r"(AKIA[0-9A-Z]{16})"),
    re.compile(r"(sk-[A-Za-z0-9]{16,})"),
    re.compile(r"(ghp_[A-Za-z0-9]{16,})"),
    re.compile(r"([A-Za-z0-9_\-]*(?:key|secret|token|password|passwd)[A-Za-z0-9_\-]*\s*[=:]\s*)"
               r"['\"]?([^\s'\"]{6,})['\"]?", re.IGNORECASE),
]


class SafetyScanner:
    """Orchestrates the L1/L2 layers and fuses findings into a decision."""

    def __init__(self, policy: Optional[SafetyPolicy] = None) -> None:
        """Create a scanner.

        Args:
            policy: Active :class:`SafetyPolicy`. When ``None`` the bundled
                default policy is loaded lazily on first use, so merely
                constructing a scanner (e.g. when the guard filter is
                instantiated at import time) never reads the YAML from disk.
        """
        self._policy = policy

    @property
    def policy(self) -> SafetyPolicy:
        """Return the active policy, loading the bundled default on first use."""
        if self._policy is None:
            self._policy = default_policy()
        return self._policy

    def scan(self, scan_input: ScanInput) -> ScanReport:
        """Scan one execution request and return a structured report.

        Args:
            scan_input: The script, declared language and tool metadata.

        Returns:
            A fully populated :class:`ScanReport`, including the fused decision,
            the highest risk level, every hit, and the scan duration.
        """
        start = time.perf_counter()

        policy = self.policy

        language = scan_input.language
        if language is ScriptLanguage.UNKNOWN:
            language = self._detect_language(scan_input.script)

        hits: list[RuleHit] = []
        hits.extend(self._run_regex_layer(scan_input.script, language))
        if policy.ast_analysis:
            if language is ScriptLanguage.PYTHON:
                hits.extend(scan_python(scan_input.script, policy))
            elif language is ScriptLanguage.BASH:
                hits.extend(scan_bash(scan_input.script, policy))

        hits = self._refine_network(hits, scan_input.script)
        hits = self._dedupe(hits)

        decision, risk_level = self._fuse(hits)

        redacted = False
        if policy.redact_sensitive:
            hits, redacted = self._redact(hits)

        summary, recommendation = self._summarize(decision, risk_level, hits)
        duration_ms = (time.perf_counter() - start) * 1000.0

        return ScanReport(
            tool_name=scan_input.tool_name,
            language=language,
            decision=decision,
            risk_level=risk_level,
            hits=hits,
            summary=summary,
            recommendation=recommendation,
            redacted=redacted,
            duration_ms=round(duration_ms, 3),
        )

    # -- L1 regex layer --------------------------------------------------------
    def _run_regex_layer(self, script: str, language: ScriptLanguage) -> list[RuleHit]:
        """Run every applicable regex rule against each line of the script."""
        hits: list[RuleHit] = []
        rules = [(rule, rule.compiled()) for rule in self.policy.rules_for(language)]
        for lineno, raw_line in enumerate(script.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            for rule, pattern in rules:
                match = pattern.search(line)
                if match:
                    hits.append(
                        RuleHit(
                            rule_id=rule.rule_id,
                            category=rule.category,
                            risk_level=rule.risk_level,
                            title=rule.title,
                            evidence=line,
                            line=lineno,
                            recommendation=rule.recommendation,
                            layer="regex",
                        ))
        return hits

    # -- network domain awareness ---------------------------------------------
    def _refine_network(self, hits: list[RuleHit], script: str) -> list[RuleHit]:
        """Downgrade or drop generic network hits using the domain allow-list.

        The destination is resolved **per hit line** rather than across the
        whole script, so a whitelisted URL on one line can no longer mask a
        risky egress on another:

        - Every destination on the hit's line allow-listed -> drop (allowed).
        - A non-listed destination on the line -> keep as high (deny).
        - No destination statically determinable on the line -> downgrade to
          medium (review), honouring "uncertain is never silently allowed".
        """
        lines = script.splitlines()
        refined: list[RuleHit] = []
        for hit in hits:
            if hit.category is not RiskCategory.NETWORK_EXFILTRATION or hit.rule_id not in _REFINABLE_NETWORK_RULES:
                refined.append(hit)
                continue
            line_text = lines[hit.line - 1] if hit.line and 1 <= hit.line <= len(lines) else ""
            domains = [m.group(1).split("@")[-1].split(":")[0] for m in _URL_RE.finditer(line_text)]
            if not domains:
                refined.append(
                    hit.model_copy(update={
                        "risk_level": RiskLevel.MEDIUM,
                        "recommendation": (hit.recommendation +
                                           " Destination could not be verified against the allow-list; review."),
                    }))
                continue
            non_whitelisted = [d for d in domains if not self.policy.domain_allowed(d)]
            if non_whitelisted:
                refined.append(
                    hit.model_copy(update={
                        "risk_level": RiskLevel.HIGH,
                        "evidence": f"{hit.evidence} -> {', '.join(sorted(set(non_whitelisted)))}",
                    }))
            # else: every domain on the line is allow-listed -> drop the hit.
        return refined

    # -- overlap de-duplication ------------------------------------------------
    @staticmethod
    def _dedupe(hits: list[RuleHit]) -> list[RuleHit]:
        """Collapse redundant findings so a report reads cleanly.

        Two reductions are applied:

        1. Exact ``(rule_id, line)`` repeats are removed.
        2. A coarse L1 *regex* hit is dropped when a **strictly higher**
           severity L2 *ast* hit covers the same ``(category, line)`` — the
           syntax-aware layer already describes that construct more precisely
           (e.g. regex ``PS004`` medium is subsumed by ast ``AST001`` high).
           Equal-severity findings are kept so no distinct evidence is lost.
        """
        seen: set[tuple[str, Optional[int]]] = set()
        unique: list[RuleHit] = []
        for hit in hits:
            key = (hit.rule_id, hit.line)
            if key in seen:
                continue
            seen.add(key)
            unique.append(hit)

        ast_cover: dict[tuple[RiskCategory, int], int] = {}
        for hit in unique:
            if hit.layer == "ast" and hit.line is not None:
                cover_key = (hit.category, hit.line)
                ast_cover[cover_key] = max(ast_cover.get(cover_key, 0), hit.risk_level.weight)

        result: list[RuleHit] = []
        for hit in unique:
            if hit.layer == "regex" and hit.line is not None:
                cover_key = (hit.category, hit.line)
                if ast_cover.get(cover_key, 0) > hit.risk_level.weight:
                    continue
            result.append(hit)
        return result

    # -- L3 decision fusion ----------------------------------------------------
    @staticmethod
    def _fuse(hits: list[RuleHit]) -> tuple[SafetyDecision, RiskLevel]:
        """Aggregate hits into a tri-state decision and the peak risk level."""
        if not hits:
            return SafetyDecision.ALLOW, RiskLevel.LOW
        peak = max((hit.risk_level for hit in hits), key=lambda level: level.weight)
        if peak.weight >= RiskLevel.HIGH.weight:
            decision = SafetyDecision.DENY
        elif peak is RiskLevel.MEDIUM:
            decision = SafetyDecision.NEEDS_HUMAN_REVIEW
        else:
            decision = SafetyDecision.ALLOW
        return decision, peak

    # -- redaction -------------------------------------------------------------
    @staticmethod
    def _redact(hits: list[RuleHit]) -> tuple[list[RuleHit], bool]:
        """Mask secret-looking substrings in every hit's evidence."""
        redacted_any = False
        out: list[RuleHit] = []
        for hit in hits:
            masked = _mask_secrets(hit.evidence)
            if masked != hit.evidence:
                redacted_any = True
                out.append(hit.model_copy(update={"evidence": masked}))
            else:
                out.append(hit)
        return out, redacted_any

    # -- summary ---------------------------------------------------------------
    @staticmethod
    def _summarize(decision: SafetyDecision, risk_level: RiskLevel,
                   hits: list[RuleHit]) -> tuple[str, str]:
        """Produce a human-readable summary and an aggregate recommendation."""
        if decision is SafetyDecision.ALLOW and not hits:
            return "No blocking risks detected.", ""
        categories = sorted({hit.category.value for hit in hits})
        summary = (f"{decision.value}: {len(hits)} finding(s) at peak risk "
                   f"'{risk_level.value}' across categories {categories}.")
        # Recommendation comes from the highest-severity hit.
        top = max(hits, key=lambda hit: hit.risk_level.weight)
        return summary, top.recommendation

    # -- language detection ----------------------------------------------------
    @staticmethod
    def _detect_language(script: str) -> ScriptLanguage:
        """Best-effort language guess when the caller does not declare one."""
        stripped = script.lstrip()
        if stripped.startswith("#!"):
            first_line = stripped.splitlines()[0]
            if re.search(r"\b(bash|sh|zsh)\b", first_line):
                return ScriptLanguage.BASH
            if "python" in first_line:
                return ScriptLanguage.PYTHON
        if _BASH_HINTS.search(script):
            return ScriptLanguage.BASH
        try:
            ast.parse(script)
            return ScriptLanguage.PYTHON
        except SyntaxError:
            return ScriptLanguage.BASH


def _mask_secrets(text: str) -> str:
    """Replace secret-looking substrings with a masked placeholder."""
    masked = text
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            masked = pattern.sub(lambda m: f"{m.group(1)}***", masked)
        else:
            masked = pattern.sub("***", masked)
    return masked
