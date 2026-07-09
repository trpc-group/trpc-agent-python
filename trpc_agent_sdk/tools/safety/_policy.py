# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Policy model and YAML loader with strict validation."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

import yaml

from trpc_agent_sdk.tools.safety._rules import DEFAULT_RULE_POLICIES
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import RiskLevel

_VALID_RISK = {r.name for r in RiskLevel}
_VALID_DECISION = {d.name for d in Decision if d != Decision.UNDECIDED}

DEFAULT_POLICY_PATH = Path(__file__).parent / "tool_safety_policy.yaml"


@dataclass
class Rule:
    """A single safety rule's static metadata."""

    id: str
    risk_level: RiskLevel
    decision: Decision
    config: dict[str, str] = field(default_factory=dict)


@dataclass
class Policy:
    """Resolved policy consumed by scanners and the decision aggregator."""

    name: str
    description: str
    rules: dict[str, Rule]
    whitelisted_domains: list[str]
    allowed_commands: list[str]
    denied_paths: list[str]
    max_timeout_seconds: int
    max_output_bytes: int
    deny_risk_level: RiskLevel
    review_risk_level: RiskLevel
    max_evidence_chars: int


def load_policy(path: str | Path | None = None) -> Policy:
    """Load a policy from YAML, applying defaults and strict validation.

    Raises:
        ValueError: on unknown fields, bad enum names, or negative numbers.
    """
    yaml_path = Path(path) if path else DEFAULT_POLICY_PATH
    raw: dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    return _policy_from_dict(raw)


def _policy_from_dict(raw: dict[str, Any]) -> Policy:
    _reject_unknown_top_level(raw)
    rule_overrides = raw.get("rule_overrides", {}) or {}
    rules = _build_rules(rule_overrides)
    return Policy(
        name=str(raw.get("name", "default")),
        description=str(raw.get("description", "")),
        rules=rules,
        whitelisted_domains=_string_list(raw, "whitelisted_domains"),
        allowed_commands=_string_list(raw, "allowed_commands"),
        denied_paths=_string_list(raw, "denied_paths"),
        max_timeout_seconds=_non_neg_int(raw, "max_timeout_seconds", 30),
        max_output_bytes=_non_neg_int(raw, "max_output_bytes", 1_048_576),
        deny_risk_level=_risk(raw, "deny_risk_level", RiskLevel.HIGH),
        review_risk_level=_risk(raw, "review_risk_level", RiskLevel.MEDIUM),
        max_evidence_chars=_non_neg_int(raw, "max_evidence_chars", 200),
    )


_ALLOWED_TOP_LEVEL = {
    "name", "description", "whitelisted_domains", "allowed_commands",
    "denied_paths", "max_timeout_seconds", "max_output_bytes", "max_evidence_chars",
    "deny_risk_level", "review_risk_level", "rule_overrides",
}


def _reject_unknown_top_level(raw: dict[str, Any]) -> None:
    unknown = set(raw.keys()) - _ALLOWED_TOP_LEVEL
    if unknown:
        raise ValueError(f"Unknown policy fields: {sorted(unknown)}")


def _build_rules(overrides: dict[str, Any]) -> dict[str, Rule]:
    rules: dict[str, Rule] = {}
    for rule_id, default in DEFAULT_RULE_POLICIES.items():
        risk, decision = default
        ov = overrides.get(rule_id, {}) or {}
        if ov:
            allowed = {"risk_level", "decision", "config"}
            bad = set(ov.keys()) - allowed
            if bad:
                raise ValueError(f"Unknown override fields for {rule_id}: {sorted(bad)}")
        risk_name = ov.get("risk_level", risk.name)
        risk = _risk({"risk_level": risk_name}, "risk_level", risk)
        dec_name = ov.get("decision", decision.name)
        dec = _decision(dec_name)
        config = {str(k): str(v) for k, v in (ov.get("config", {}) or {}).items()}
        rules[rule_id] = Rule(id=rule_id, risk_level=risk, decision=dec, config=config)
    return rules


def _non_neg_int(raw: dict[str, Any], key: str, default: int) -> int:
    val = raw.get(key, default)
    if not isinstance(val, int) or isinstance(val, bool) or val < 0:
        raise ValueError(f"{key} must be a non-negative integer, got {val!r}")
    return val


def _string_list(raw: dict[str, Any], key: str) -> list[str]:
    """Validate that a field contains only strings."""
    vals = raw.get(key, [])
    if not isinstance(vals, list):
        raise ValueError(f"{key} must be a list, got {type(vals).__name__}: {vals!r}")
    non_strings = [v for v in vals if not isinstance(v, str)]
    if non_strings:
        raise ValueError(f"{key} must contain only strings; found non-string elements: {non_strings!r}")
    return [v.lower() for v in vals]


def _risk(raw: dict[str, Any], key: str, default: RiskLevel) -> RiskLevel:
    name = raw.get(key, default.name)
    if not isinstance(name, str):
        raise ValueError(f"{key} must be a string enum name (one of {sorted(_VALID_RISK)}), got {type(name).__name__}: {name!r}")
    if name not in _VALID_RISK:
        raise ValueError(f"{key} must be one of {sorted(_VALID_RISK)}, got {name!r}")
    return RiskLevel[name]


def _decision(name: str) -> Decision:
    if not isinstance(name, str):
        raise ValueError(f"decision must be a string enum name (one of {sorted(_VALID_DECISION)}), got {type(name).__name__}: {name!r}")
    if name not in _VALID_DECISION:
        raise ValueError(f"decision must be one of {sorted(_VALID_DECISION)}, got {name!r}")
    return Decision[name]
