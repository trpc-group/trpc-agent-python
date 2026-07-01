# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy loader and validator for the Tool Script Safety Guard.

Loads ``tool_safety_policy.yaml``, validates required sections, and
exposes a ``SafetyPolicy`` data-class that the scanner and rules consume.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Optional

import yaml

from trpc_agent_sdk.log import logger

from ._types import Decision
from ._types import RiskLevel

# Default location — can be overridden via env var or constructor arg.
_DEFAULT_POLICY_PATH = Path(__file__).resolve().parent / "tool_safety_policy.yaml"


def _env_policy_path() -> str:
    return os.environ.get("TOOL_SAFETY_POLICY_PATH", str(_DEFAULT_POLICY_PATH))


# ---------------------------------------------------------------------------
# Policy model
# ---------------------------------------------------------------------------


@dataclass
class SafetyPolicy:
    """Deserialised and validated safety policy configuration."""

    # Global
    max_script_lines: int = 500
    max_script_bytes: int = 524288
    max_timeout_seconds: int = 300
    max_output_bytes: int = 10485760

    # Decision map
    decision_thresholds: dict[str, str] = field(default_factory=lambda: {
        "critical": "deny",
        "high": "deny",
        "medium": "needs_human_review",
        "low": "allow",
        "info": "allow",
    })

    # Whitelists
    whitelist_domains: list[str] = field(default_factory=list)
    whitelist_commands: list[str] = field(default_factory=list)
    whitelist_patterns: list[str] = field(default_factory=list)

    # Blocklists
    blocklist_paths: list[str] = field(default_factory=list)
    blocklist_env_vars: list[str] = field(default_factory=list)
    blocklist_commands: list[str] = field(default_factory=list)
    blocklist_patterns: list[str] = field(default_factory=list)

    # Rule configs (raw dicts keyed by rule section name)
    rule_configs: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Allow patterns
    allow_patterns: list[str] = field(default_factory=list)

    # Sanitization
    mask_secrets_in_reports: bool = True
    mask_string: str = "***REDACTED***"

    # Source path for versioning
    source_path: str = ""
    content_hash: str = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def decision_for(self, risk_level: RiskLevel) -> Decision:
        """Return the configured decision for *risk_level*."""
        mapping = self.decision_thresholds
        key = risk_level.value
        decision_str = mapping.get(key, "needs_human_review")
        try:
            return Decision(decision_str)
        except ValueError:
            return Decision.NEEDS_HUMAN_REVIEW

    def is_domain_whitelisted(self, domain: str) -> bool:
        """Check whether *domain* matches a whitelist entry (glob-aware)."""
        import fnmatch
        for entry in self.whitelist_domains:
            if fnmatch.fnmatch(domain, entry):
                return True
        return False

    def is_command_whitelisted(self, command: str) -> bool:
        """Check whether *command* is in the whitelist or matches a glob."""
        import fnmatch
        for entry in self.whitelist_commands:
            if fnmatch.fnmatch(command, entry):
                return True
        return False


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class PolicyLoader:
    """Loads and validates a ``SafetyPolicy`` from a YAML file."""

    def __init__(self, policy_path: Optional[str] = None) -> None:
        self._policy_path = policy_path or _env_policy_path()
        self._raw: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> SafetyPolicy:
        """Read, parse, validate, and return a ``SafetyPolicy``.

        Returns:
            SafetyPolicy ready for consumption.
        """
        self._raw = self._read_yaml()
        self._validate()
        return self._build()

    def reload(self) -> SafetyPolicy:
        """Reload the policy from disk (useful for hot-reload scenarios)."""
        logger.info("Reloading safety policy from %s", self._policy_path)
        return self.load()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_yaml(self) -> dict[str, Any]:
        path = Path(self._policy_path)
        if not path.exists():
            logger.warning("Safety policy file not found at %s; using defaults.", path)
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}

    def _validate(self) -> None:
        """Basic structural validation — logs warnings for missing sections."""
        required_top = ["global", "decision_thresholds", "whitelists", "blocklists", "rules"]
        for key in required_top:
            if key not in self._raw:
                logger.warning("Safety policy missing top-level section '%s'; using defaults.", key)

    def _build(self) -> SafetyPolicy:
        raw = self._raw

        # --- Global ---
        g = raw.get("global", {})
        policy = SafetyPolicy(
            max_script_lines=int(g.get("max_script_lines", 500)),
            max_script_bytes=int(g.get("max_script_bytes", 524288)),
            max_timeout_seconds=int(g.get("max_timeout_seconds", 300)),
            max_output_bytes=int(g.get("max_output_bytes", 10485760)),
        )

        # --- Decision thresholds ---
        dt = raw.get("decision_thresholds", {})
        if dt:
            policy.decision_thresholds = {k: v for k, v in dt.items() if k in {r.value for r in RiskLevel}}

        # --- Whitelists ---
        wl = raw.get("whitelists", {})
        policy.whitelist_domains = [str(d) for d in wl.get("domains", [])]
        policy.whitelist_commands = [str(c) for c in wl.get("commands", [])]
        policy.whitelist_patterns = [str(p) for p in wl.get("patterns", [])]

        # --- Blocklists ---
        bl = raw.get("blocklists", {})
        policy.blocklist_paths = [str(p) for p in bl.get("paths", [])]
        policy.blocklist_env_vars = [str(e) for e in bl.get("env_vars", [])]
        policy.blocklist_commands = [str(c) for c in bl.get("commands", [])]
        policy.blocklist_patterns = [str(p) for p in bl.get("patterns", [])]

        # --- Rules ---
        policy.rule_configs = raw.get("rules", {})

        # --- Allow patterns ---
        policy.allow_patterns = [str(p) for p in raw.get("allow_patterns", [])]

        # --- Sanitization ---
        san = raw.get("sanitization", {})
        policy.mask_secrets_in_reports = bool(san.get("mask_secrets_in_reports", True))
        policy.mask_string = str(san.get("mask_string", "***REDACTED***"))

        # --- Versioning ---
        policy.source_path = str(self._policy_path)
        policy.content_hash = self._compute_hash()

        return policy

    def _compute_hash(self) -> str:
        """Return a short hash of the raw YAML content for version tracking."""
        try:
            path = Path(self._policy_path)
            if path.exists():
                raw_bytes = path.read_bytes()
                return hashlib.sha256(raw_bytes).hexdigest()[:12]
        except Exception:  # pylint: disable=broad-except
            pass
        return "unknown"


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_policy: Optional[SafetyPolicy] = None


def get_policy(policy_path: Optional[str] = None) -> SafetyPolicy:
    """Return the cached policy or load it from disk on first call."""
    global _default_policy  # pylint: disable=global-statement
    if _default_policy is None:
        _default_policy = PolicyLoader(policy_path).load()
    return _default_policy


def reload_policy(policy_path: Optional[str] = None) -> SafetyPolicy:
    """Force-reload the policy from disk."""
    global _default_policy  # pylint: disable=global-statement
    _default_policy = PolicyLoader(policy_path).load()
    return _default_policy
