# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Declarative safety policy model and loader.

The whole rule set lives in YAML so that operators can add rules, tweak
severity, extend allow-lists or forbidden paths **without touching code** — the
"策略文件修改后不需要改代码" acceptance criterion.

A :class:`SafetyPolicy` is a validated pydantic model. :func:`load_policy`
resolves a YAML path (falling back to the bundled ``_default_policy.yaml``) and
raises a clear error on malformed input.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from typing import Optional

import yaml
from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator

from ._types import RiskCategory
from ._types import RiskLevel
from ._types import ScriptLanguage

_DEFAULT_POLICY_PATH = Path(__file__).with_name("_default_policy.yaml")


def _is_path_token_char(ch: str) -> bool:
    """Whether ``ch`` extends an identifier-like token (letters/digits/_).

    Path separators (``/``, ``.``, ``-``, ``~``), whitespace, quotes and the
    empty string (start/end of text) are treated as token boundaries.
    """
    return ch.isalnum() or ch == "_"


def _forbidden_fragment_present(fragment: str, lowered: str) -> bool:
    """Whether ``fragment`` appears in ``lowered`` at token boundaries.

    A bare substring test flags harmless tokens that merely embed a forbidden
    fragment; requiring the character on each side of the match to be a token
    boundary keeps real paths matched while dropping those false positives.
    """
    if not fragment:
        return False
    flen = len(fragment)
    start = 0
    while True:
        idx = lowered.find(fragment, start)
        if idx == -1:
            return False
        before = lowered[idx - 1] if idx > 0 else ""
        after = lowered[idx + flen] if idx + flen < len(lowered) else ""
        if not _is_path_token_char(before) and not _is_path_token_char(after):
            return True
        start = idx + 1


class RegexRule(BaseModel):
    """A single L1 regex rule declared in the policy file."""

    rule_id: str = Field(description="Stable rule identifier, unique within the policy.")
    category: RiskCategory = Field(description="Risk family this rule belongs to.")
    risk_level: RiskLevel = Field(description="Severity assigned when this rule matches.")
    title: str = Field(description="Short human-readable title.")
    pattern: str = Field(description="Python regex evaluated against each line.")
    language: ScriptLanguage = Field(default=ScriptLanguage.UNKNOWN,
                                     description="Restrict rule to a language; UNKNOWN means both.")
    recommendation: str = Field(default="", description="Suggested remediation.")
    flags_ignorecase: bool = Field(default=True, description="Whether the regex is case-insensitive.")

    @field_validator("pattern")
    @classmethod
    def _validate_pattern(cls, value: str) -> str:
        """Reject rules whose regex does not compile, failing fast at load time."""
        try:
            re.compile(value)
        except re.error as exc:  # pragma: no cover - defensive
            raise ValueError(f"invalid regex pattern: {value!r} ({exc})") from exc
        return value

    def compiled(self) -> re.Pattern[str]:
        """Return the compiled pattern honouring the ignorecase flag."""
        return re.compile(self.pattern, re.IGNORECASE if self.flags_ignorecase else 0)

    def applies_to(self, language: ScriptLanguage) -> bool:
        """Whether this rule should run for the given script language."""
        if self.language is ScriptLanguage.UNKNOWN:
            return True
        return self.language is language


class SafetyPolicy(BaseModel):
    """Full declarative policy governing the scanner's behaviour."""

    version: str = Field(default="1", description="Policy schema version.")
    allowed_domains: list[str] = Field(default_factory=list, description="Domains that network access may target.")
    allowed_commands: list[str] = Field(
        default_factory=list,
        description="(Reserved) Safe shell commands; consumed by runtime/sandbox layers, "
        "not enforced by the static scanner.")
    forbidden_paths: list[str] = Field(default_factory=list,
                                       description="Path fragments that must never be read or written.")
    max_timeout_seconds: int = Field(default=60,
                                     description="(Reserved) Max execution timeout for runtime enforcement.")
    max_output_bytes: int = Field(default=1_048_576, description="(Reserved) Max output size for runtime enforcement.")
    redact_sensitive: bool = Field(default=True, description="Whether to mask evidence in reports/audit.")
    ast_analysis: bool = Field(default=True, description="Whether to run the L2 syntax-aware layer.")
    rules: list[RegexRule] = Field(default_factory=list, description="L1 regex rule set.")

    @field_validator("rules")
    @classmethod
    def _unique_rule_ids(cls, value: list[RegexRule]) -> list[RegexRule]:
        """Ensure rule ids are unique so that hits map back unambiguously."""
        seen: set[str] = set()
        for rule in value:
            if rule.rule_id in seen:
                raise ValueError(f"duplicate rule_id in policy: {rule.rule_id}")
            seen.add(rule.rule_id)
        return value

    def domain_allowed(self, domain: str) -> bool:
        """Whether ``domain`` (or a subdomain of it) is on the allow-list."""
        domain = domain.strip().lower().rstrip(".")
        for allowed in self.allowed_domains:
            allowed = allowed.strip().lower().rstrip(".")
            if domain == allowed or domain.endswith("." + allowed):
                return True
        return False

    def forbidden_path_matches(self, text: str) -> list[str]:
        """Return the forbidden-path fragments ``text`` references.

        Matching is token-boundary aware so a harmless token that merely embeds
        a fragment (``config.env`` for ``.env``; ``id_rsa_note`` for ``id_rsa``)
        is not treated as sensitive-path access, while genuine paths
        (``/app/.env``, ``~/.ssh/id_rsa``) still match. Both the Bash (L2) and
        Python (L2) layers share this so the two stay consistent.
        """
        lowered = text.lower()
        return [f for f in self.forbidden_paths if _forbidden_fragment_present(f.lower(), lowered)]

    def rules_for(self, language: ScriptLanguage) -> list[RegexRule]:
        """Return the subset of regex rules applicable to ``language``."""
        return [rule for rule in self.rules if rule.applies_to(language)]


def load_policy(path: Optional[str | Path] = None) -> SafetyPolicy:
    """Load a :class:`SafetyPolicy` from YAML.

    Args:
        path: Path to a policy YAML file. When ``None`` the bundled default
            policy is loaded.

    Returns:
        A validated :class:`SafetyPolicy`.

    Raises:
        FileNotFoundError: If an explicit ``path`` does not exist.
        ValueError: If the YAML is malformed or violates the schema.
    """
    policy_path = Path(path) if path is not None else _DEFAULT_POLICY_PATH
    if not policy_path.exists():
        raise FileNotFoundError(f"safety policy file not found: {policy_path}")

    try:
        raw: Any = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"failed to parse policy YAML {policy_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(f"policy YAML must be a mapping, got {type(raw).__name__}")

    try:
        return SafetyPolicy.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError and friends
        raise ValueError(f"invalid safety policy {policy_path}: {exc}") from exc


def default_policy() -> SafetyPolicy:
    """Return the bundled default policy."""
    return load_policy(None)
