# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy loading for the Tool Script Safety Guard.

The policy is the single place where operators tune behaviour **without
touching code** (acceptance requirement 6): allow-listed egress domains,
forbidden paths, allowed commands, decision thresholds, the tool/param to
scanner mapping, redaction and scan limits.

Loading semantics (see design doc 6.1):

- An *explicitly requested* policy file (``load_policy(path=...)`` or the
  ``TOOL_SAFETY_POLICY_PATH`` environment variable) that is missing, malformed
  or schema-invalid causes a **fail-fast** :class:`PolicyError`. We never run a
  security component on a half-loaded config.
- When **no** path is supplied we fall back to a conservative, code-defined
  default policy so the guard is always usable out of the box.
- The policy path is only ever read from explicit configuration or the
  environment variable, never from untrusted tool input.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Optional

import yaml

# Environment variable that points at a policy file. Trusted source only.
ENV_POLICY_PATH = "TOOL_SAFETY_POLICY_PATH"

# Hard ceilings to keep the scanner itself from becoming an attack surface
# (ReDoS / memory) regardless of what a policy file says.
_ABSOLUTE_MAX_INPUT_SIZE = 5_000_000
_ABSOLUTE_MAX_LINE_LENGTH = 100_000


class PolicyError(Exception):
    """Raised when an explicitly requested policy cannot be loaded safely."""


@dataclass
class ScanLimits:
    """Bounds that protect the scanner from pathological input (ReDoS / OOM)."""

    max_input_size: int = 1_000_000
    max_line_length: int = 4_000

    def clamp(self) -> "ScanLimits":
        self.max_input_size = max(1, min(self.max_input_size, _ABSOLUTE_MAX_INPUT_SIZE))
        self.max_line_length = max(1, min(self.max_line_length, _ABSOLUTE_MAX_LINE_LENGTH))
        return self


@dataclass
class RedactConfig:
    """How ``evidence`` snippets are masked before leaving the process."""

    enabled: bool = True
    mask: str = "***REDACTED***"
    # Extra regexes (on top of the built-in secret patterns) whose matches are
    # masked inside evidence snippets.
    patterns: list[str] = field(default_factory=list)


@dataclass
class ParamGroup:
    """Maps a tool-name keyword to the arg keys / scanner language to use."""

    keys: list[str]
    language: str


@dataclass
class SafetyPolicy:
    """Operator-tunable policy. All fields are hot-reloadable via YAML."""

    allow_domains: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    forbidden_paths: list[str] = field(default_factory=list)
    max_timeout: int = 300
    max_output_size: int = 1_000_000
    # Maps a RiskLevel value ("critical"/"high"/"medium"/"low") to the decision
    # it escalates to ("deny"/"needs_human_review"/"allow"). Changing this is
    # how an operator retunes severity without code changes.
    decision_thresholds: dict[str, str] = field(
        default_factory=lambda: {
            "critical": "deny",
            "high": "deny",
            "medium": "needs_human_review",
            "low": "allow",
        }
    )
    # tool-name keyword -> ParamGroup; drives which scanner reads which arg key.
    param_keys: dict[str, ParamGroup] = field(default_factory=dict)
    redact: RedactConfig = field(default_factory=RedactConfig)
    scan_limits: ScanLimits = field(default_factory=ScanLimits)

    # ------------------------------------------------------------------ #
    # Convenience accessors
    # ------------------------------------------------------------------ #
    def is_domain_allowed(self, domain: str) -> bool:
        """True if ``domain`` (host only) is on the egress allow-list.

        Matching is case-insensitive and allows exact host or sub-domain of an
        allow-listed host.
        """
        host = (domain or "").strip().lower()
        if not host:
            return False
        for allowed in self.allow_domains:
            allowed = allowed.strip().lower()
            if not allowed:
                continue
            if host == allowed or host.endswith("." + allowed):
                return True
        return False

    @classmethod
    def default(cls) -> "SafetyPolicy":
        """Conservative built-in policy used when no file is configured.

        The egress allow-list is intentionally empty: with no configured
        policy, *any* external network call is treated as non-allow-listed.
        """
        return cls(
            allow_domains=[],
            allowed_commands=[
                "ls", "pwd", "cat", "grep", "find", "head", "tail", "wc",
                "echo", "python", "python3", "pytest", "git",
            ],
            forbidden_paths=[
                "/", "/etc", "/dev", "/boot", "/sys", "/proc",
                "~/.ssh", "~/.aws", "~/.config/gcloud",
            ],
            param_keys={
                "bash": ParamGroup(keys=["command"], language="bash"),
                "shell": ParamGroup(keys=["command"], language="bash"),
                "code": ParamGroup(keys=["code", "source"], language="python"),
                "python": ParamGroup(keys=["code", "source"], language="python"),
                "exec": ParamGroup(keys=["code", "source", "command"], language="python"),
            },
        )


# ---------------------------------------------------------------------------- #
# Loading & validation
# ---------------------------------------------------------------------------- #
def _validate_regexes(patterns: list[str], where: str) -> None:
    """Fail-fast if any operator-supplied regex does not compile (ReDoS guard)."""
    for pat in patterns:
        try:
            re.compile(pat)
        except re.error as ex:
            raise PolicyError(f"invalid regex in {where}: {pat!r} ({ex})") from ex


def _parse_param_keys(raw: Any) -> dict[str, ParamGroup]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise PolicyError("'param_keys' must be a mapping")
    groups: dict[str, ParamGroup] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict) or "keys" not in spec or "language" not in spec:
            raise PolicyError(f"'param_keys.{name}' must define 'keys' and 'language'")
        keys = spec["keys"]
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise PolicyError(f"'param_keys.{name}.keys' must be a list of strings")
        groups[str(name)] = ParamGroup(keys=list(keys), language=str(spec["language"]))
    return groups


def _policy_from_dict(data: dict[str, Any]) -> SafetyPolicy:
    """Build and validate a :class:`SafetyPolicy` from parsed YAML."""
    if not isinstance(data, dict):
        raise PolicyError("policy root must be a mapping")

    policy = SafetyPolicy.default()

    if "allow_domains" in data:
        policy.allow_domains = list(data["allow_domains"] or [])
    if "allowed_commands" in data:
        policy.allowed_commands = list(data["allowed_commands"] or [])
    if "forbidden_paths" in data:
        policy.forbidden_paths = list(data["forbidden_paths"] or [])
    if "max_timeout" in data:
        policy.max_timeout = int(data["max_timeout"])
    if "max_output_size" in data:
        policy.max_output_size = int(data["max_output_size"])
    if "decision_thresholds" in data and data["decision_thresholds"]:
        thresholds = {str(k).lower(): str(v).lower() for k, v in data["decision_thresholds"].items()}
        valid_decisions = {"allow", "deny", "needs_human_review"}
        for level, decision in thresholds.items():
            if decision not in valid_decisions:
                raise PolicyError(
                    f"decision_thresholds.{level} must be one of {sorted(valid_decisions)}, got {decision!r}"
                )
        policy.decision_thresholds.update(thresholds)
    if "param_keys" in data:
        policy.param_keys = _parse_param_keys(data["param_keys"])

    redact_raw = data.get("redact")
    if redact_raw:
        if not isinstance(redact_raw, dict):
            raise PolicyError("'redact' must be a mapping")
        patterns = list(redact_raw.get("patterns", []) or [])
        _validate_regexes(patterns, "redact.patterns")
        policy.redact = RedactConfig(
            enabled=bool(redact_raw.get("enabled", True)),
            mask=str(redact_raw.get("mask", "***REDACTED***")),
            patterns=patterns,
        )

    limits_raw = data.get("scan_limits")
    if limits_raw:
        if not isinstance(limits_raw, dict):
            raise PolicyError("'scan_limits' must be a mapping")
        policy.scan_limits = ScanLimits(
            max_input_size=int(limits_raw.get("max_input_size", 1_000_000)),
            max_line_length=int(limits_raw.get("max_line_length", 4_000)),
        )
    policy.scan_limits.clamp()
    return policy


def load_policy(path: Optional[str] = None) -> SafetyPolicy:
    """Load a :class:`SafetyPolicy`.

    Resolution order:
        1. explicit ``path`` argument,
        2. ``TOOL_SAFETY_POLICY_PATH`` environment variable,
        3. built-in conservative default.

    An explicitly requested file (cases 1 and 2) that cannot be read, parsed or
    validated raises :class:`PolicyError` (fail-fast). Case 3 never raises.
    """
    resolved = path or os.environ.get(ENV_POLICY_PATH)
    if not resolved:
        return SafetyPolicy.default()

    policy_path = Path(resolved)
    if not policy_path.is_file():
        raise PolicyError(f"policy file not found: {resolved}")
    try:
        raw_text = policy_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text)
    except (OSError, yaml.YAMLError) as ex:
        raise PolicyError(f"failed to read/parse policy file {resolved}: {ex}") from ex
    if data is None:
        raise PolicyError(f"policy file is empty: {resolved}")
    return _policy_from_dict(data)
