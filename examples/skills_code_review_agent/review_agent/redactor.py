# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Secret redaction for reports, database rows and tool output.

Design:
* value-only masking — key names and surrounding context stay readable so a
  finding's evidence keeps enough information to act on;
* an allowlist keeps documented placeholder credentials (AWS doc samples,
  ``example``/``test`` values) readable, so redaction does not destroy test
  fixtures or docs;
* applied at three places: the agent-level ``after_tool_callback`` (anything
  a model might echo), before findings are persisted, and before reports are
  rendered.  Belt and braces on purpose — acceptance requires no plaintext
  secret anywhere in DB or reports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MASK = "***REDACTED***"

# Values that are explicitly safe to keep (documented sample credentials).
_ALLOWLIST_LITERALS = (
    "AKIAIOSFODNN7EXAMPLE",
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
)
_ALLOWLIST_HINT = re.compile(r"(?i)example|sample|dummy|placeholder|fake|changeme|your[-_]|<[^>]+>|\$\{[^}]*\}")


@dataclass
class _Rule:
    name: str
    pattern: re.Pattern
    # group index holding the secret value; 0 = whole match
    group: int = 0


def _r(name: str, pattern: str, group: int = 0, flags: int = 0) -> _Rule:
    return _Rule(name=name, pattern=re.compile(pattern, flags), group=group)


# 20+ secret formats.  Order matters: specific formats before generic ones.
_RULES: list[_Rule] = [
    _r("aws_access_key_id", r"\bAKIA[0-9A-Z]{16}\b"),
    _r("aws_secret_key", r"(?i)\baws.{0,20}?['\"]([A-Za-z0-9/+=]{40})['\"]", group=1),
    _r("github_token", r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b"),
    _r("github_pat", r"\bgithub_pat_[A-Za-z0-9_]{22,255}\b"),
    _r("gitlab_pat", r"\bglpat-[A-Za-z0-9_\-]{20,}\b"),
    _r("slack_token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b"),
    _r("stripe_key", r"\b[srp]k_(?:live|test)_[A-Za-z0-9]{20,}\b"),
    _r("google_api_key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    _r("anthropic_key", r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),
    _r("openai_key", r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"),
    _r("npm_token", r"\bnpm_[A-Za-z0-9]{36}\b"),
    _r("pypi_token", r"\bpypi-[A-Za-z0-9_\-]{20,}\b"),
    _r("sendgrid_key", r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}\b"),
    _r("twilio_key", r"\bSK[0-9a-fA-F]{32}\b"),
    _r("telegram_bot", r"\b\d{8,10}:AA[A-Za-z0-9_\-]{30,}\b"),
    _r("jwt", r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}\b"),
    _r(
        "private_key_block", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----|"
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{0,1200}"),
    _r("url_password", r"\b([a-z][a-z0-9+.\-]*://[^/\s:@'\"]+:)([^@\s/'\"]{3,})@", group=2),
    _r("azure_account_key", r"(?i)AccountKey=([A-Za-z0-9/+=]{40,})", group=1),
    _r("basic_auth_header", r"(?i)\bAuthorization['\"]?\s*[:=]\s*['\"]?Basic ([A-Za-z0-9+/=]{8,})", group=1),
    _r("bearer_token", r"(?i)\bAuthorization['\"]?\s*[:=]\s*['\"]?Bearer ([A-Za-z0-9._\-]{8,})", group=1),
    _r("heroku_api_key",
       r"(?i)\bheroku.{0,20}\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b",
       group=1),
    # generic assignment must come last: password/secret/token/api_key = "literal"
    # ([\w\-]* prefix: db_password, service_token etc. also match)
    _r("generic_assignment",
       r"(?i)\b[\w\-]*(password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key|"
       r"client[_\-]?secret|auth[_\-]?token)\b['\"]?\s*[:=]\s*['\"]([^'\"\s]{6,})['\"]",
       group=2),
]


@dataclass
class RedactionResult:
    text: str
    hits: list[dict] = field(default_factory=list)

    @property
    def hit_count(self) -> int:
        return len(self.hits)


class Redactor:
    """Value-only secret masking with an allowlist."""

    def __init__(self, extra_allowlist: tuple[str, ...] = ()) -> None:
        self._allow_literals = set(_ALLOWLIST_LITERALS) | set(extra_allowlist)

    def _allowed(self, value: str) -> bool:
        if value in self._allow_literals:
            return True
        if _ALLOWLIST_HINT.search(value):
            return True
        # all-same-character strings are placeholders (xxxxx, *****)
        if len(set(value.strip())) <= 1:
            return True
        return False

    def redact(self, text: str) -> RedactionResult:
        """Mask secret *values* in text; returns new text plus hit records."""
        if not text:
            return RedactionResult(text=text)
        hits: list[dict] = []

        for rule in _RULES:

            def _sub(match: re.Match, _rule=rule) -> str:
                value = match.group(_rule.group)
                if value is None or self._allowed(value):
                    return match.group(0)
                hits.append({"rule": _rule.name, "prefix": value[:4]})
                whole = match.group(0)
                if _rule.group == 0:
                    return MASK
                start = match.start(_rule.group) - match.start(0)
                end = match.end(_rule.group) - match.start(0)
                return whole[:start] + MASK + whole[end:]

            text = rule.pattern.sub(_sub, text)

        return RedactionResult(text=text, hits=hits)

    def redact_obj(self, obj):
        """Recursively redact every string inside dicts/lists/tuples."""
        if isinstance(obj, str):
            return self.redact(obj).text
        if isinstance(obj, dict):
            return {key: self.redact_obj(value) for key, value in obj.items()}
        if isinstance(obj, (list, tuple)):
            seq = [self.redact_obj(item) for item in obj]
            return seq if isinstance(obj, list) else tuple(seq)
        return obj
