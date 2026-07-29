#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Shared secret detection and redaction patterns.

The detector intentionally returns locations and types, never matched values.
This makes the module safe to reuse in the sandbox and by host-side output
redaction while keeping one regular-expression table as the source of truth.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable, Tuple

from .diff_parser import ChangeSet


@dataclass(frozen=True)
class SecretPatternSpec:
    """One deterministic secret pattern and its redaction label."""

    secret_type: str
    expression: str
    confidence: float = 0.95

    @property
    def pattern(self) -> re.Pattern[str]:
        """编译并返回该密钥类型对应的忽略大小写正则表达式。"""

        return re.compile(self.expression, re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class SecretMatch:
    """A non-sensitive match description for use inside a review task."""

    secret_type: str
    start: int
    end: int
    confidence: float


@dataclass(frozen=True)
class SecretLocation:
    """A redacted secret location extracted from a parsed review input."""

    secret_type: str
    file: str
    line: int
    line_side: str
    confidence: float
    evidence: str


_ASSIGNMENT_VALUE = r"(?:'[^'\n]+'|\"[^\"\n]+\"|[^\s#;]+)"

SECRET_PATTERN_SPECS: Tuple[SecretPatternSpec, ...] = (
    SecretPatternSpec("aws_access_key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    SecretPatternSpec("github_token", r"\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{20,})\b"),
    SecretPatternSpec("gitlab_token", r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    SecretPatternSpec("slack_token", r"\bxox[baprs]-\d+-\d+-[A-Za-z0-9-]{20,}\b"),
    SecretPatternSpec("openai_api_key", r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b"),
    SecretPatternSpec("stripe_secret_key", r"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}\b"),
    SecretPatternSpec("sendgrid_api_key", r"\bSG\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b"),
    SecretPatternSpec("npm_token", r"\bnpm_[A-Za-z0-9]{20,}\b"),
    SecretPatternSpec("pypi_token", r"\bpypi-[A-Za-z0-9_-]{20,}\b"),
    SecretPatternSpec("google_api_key", r"\bAIza[A-Za-z0-9_-]{20,}\b"),
    SecretPatternSpec("huggingface_token", r"\bhf_[A-Za-z0-9]{20,}\b"),
    SecretPatternSpec("terraform_token", r"\bATLAS-[A-Za-z0-9_-]{20,}\b"),
    SecretPatternSpec("digitalocean_token", r"\bdop_v1_[A-Za-z0-9]{20,}\b"),
    SecretPatternSpec("square_access_token", r"\bsq0atp-[A-Za-z0-9_-]{20,}\b"),
    SecretPatternSpec("shopify_access_token", r"\bshpat_[A-Za-z0-9_-]{20,}\b"),
    SecretPatternSpec("linear_api_key", r"\blin_api_[A-Za-z0-9_-]{20,}\b"),
    SecretPatternSpec("mailgun_api_key", r"\bkey-[A-Za-z0-9_-]{20,}\b"),
    SecretPatternSpec("jwt", r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{16,}\b"),
    SecretPatternSpec("private_key", r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
    SecretPatternSpec(
        "database_url",
        r"\b(?:postgres(?:ql)?|mysql(?:\+[A-Za-z0-9_]+)?|mongodb(?:\+srv)?|redis|amqps?)://[^\s/@:]*:[^\s/@]+@[^\s/]+",
    ),
    SecretPatternSpec("bearer_token", r"\bBearer\s+[A-Za-z0-9._-]{20,}"),
    SecretPatternSpec(
        "azure_storage_key",
        r"\bAccountKey\s*=\s*[A-Za-z0-9+/]{32,}={0,2}",
    ),
    SecretPatternSpec(
        "twilio_auth_token",
        r"\btwilio_auth_token\s*[=:]\s*" + _ASSIGNMENT_VALUE,
    ),
    SecretPatternSpec(
        "datadog_api_key",
        r"\bDD_API_KEY\s*[=:]\s*" + _ASSIGNMENT_VALUE,
    ),
    SecretPatternSpec(
        "new_relic_license_key",
        r"\bNEW_RELIC_LICENSE_KEY\s*[=:]\s*" + _ASSIGNMENT_VALUE,
    ),
    SecretPatternSpec(
        "sentry_dsn",
        r"https?://[0-9a-f]{24,}@[A-Za-z0-9.-]+/\d+\b",
    ),
    SecretPatternSpec(
        "discord_token",
        r"\b[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,}\b",
        0.92,
    ),
    SecretPatternSpec(
        "password",
        r"\b(?:[A-Za-z0-9_]*password|passwd|pwd)\s*[=:]\s*" + _ASSIGNMENT_VALUE,
        0.91,
    ),
    SecretPatternSpec(
        "token",
        r"\b(?:[A-Za-z0-9_]*token)\s*[=:]\s*" + _ASSIGNMENT_VALUE,
        0.91,
    ),
    SecretPatternSpec(
        "secret",
        r"\b(?:[A-Za-z0-9_]*secret(?:_key)?|credential)\s*[=:]\s*" + _ASSIGNMENT_VALUE,
        0.91,
    ),
)

_PLACEHOLDER_VALUES = {
    "redacted",
    "changeme",
    "example",
    "example-api-key",
    "your-token-here",
    "your_github_token_here",
    "token",
    "api-key",
}


def shannon_entropy(value: str) -> float:
    """计算给定文本的 Shannon 熵（每字符 bit 数）。"""

    if not value:
        return 0.0
    length = len(value)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in (value.count(character) for character in set(value))
    )


def _is_placeholder(value: str) -> bool:
    """判断命中内容是否仅含文档占位值，兼容 ``token='example'`` 形式。"""

    normalized = value.strip().strip("'\"").lower().strip("<>")
    if "=" in normalized or ":" in normalized:
        separator = "=" if "=" in normalized else ":"
        normalized = normalized.split(separator, maxsplit=1)[1].strip().strip("'\"").strip("<>")
    return (
        normalized in _PLACEHOLDER_VALUES
        or "redacted" in normalized
        or "changeme" in normalized
        or "your-" in normalized
        or "your_" in normalized
        or normalized.startswith("example")
        or "[redacted" in normalized
    )


def _high_entropy_matches(text: str) -> Iterable[SecretMatch]:
    """从敏感赋值语境中提取满足高熵阈值的密钥候选。"""

    assignment = re.compile(
        r"\b(?:credential|value|api[_-]?key|access[_-]?key|auth(?:orization)?)"
        r"\s*[=:]\s*(?P<value>'[^'\n]+'|\"[^\"\n]+\")",
        re.IGNORECASE | re.MULTILINE,
    )
    for candidate in assignment.finditer(text):
        value = candidate.group("value").strip("'\"")
        if len(value) >= 24 and shannon_entropy(value) >= 3.5:
            yield SecretMatch(
                secret_type="high_entropy_secret",
                start=candidate.start(),
                end=candidate.end(),
                confidence=0.96,
            )


def detect_secrets(text: str) -> Tuple[SecretMatch, ...]:
    """检测敏感信息但不向调用方返回其原始值。

    The regular-expression specs also drive ``redact_text``.  Placeholder
    examples are filtered after a pattern matches so comments containing real
    credentials remain detectable while documentation placeholders stay quiet.
    """

    matches = []
    for spec in SECRET_PATTERN_SPECS:
        for candidate in spec.pattern.finditer(text):
            if not _is_placeholder(candidate.group(0)):
                matches.append(
                    SecretMatch(
                        secret_type=spec.secret_type,
                        start=candidate.start(),
                        end=candidate.end(),
                        confidence=spec.confidence,
                    )
                )
    matches.extend(_high_entropy_matches(text))
    selected = []
    occupied_ranges = []
    for match in sorted(
        matches,
        key=lambda item: (-item.confidence, item.start, -(item.end - item.start), item.secret_type),
    ):
        if any(match.start < end and start < match.end for start, end in occupied_ranges):
            continue
        selected.append(match)
        occupied_ranges.append((match.start, match.end))
    return tuple(sorted(selected, key=lambda item: (item.start, item.end, item.secret_type)))


def redact_text(text: str) -> str:
    """将每个检测到的敏感信息替换为带类型的非敏感标记。"""

    redacted = text
    for match in sorted(detect_secrets(text), key=lambda item: (item.start, item.end), reverse=True):
        redacted = (
            redacted[:match.start]
            + f"[REDACTED:{match.secret_type}]"
            + redacted[match.end:]
        )
    return redacted


def contains_secret(text: str) -> bool:
    """返回文本中是否仍存在未脱敏的敏感信息语法。"""

    return bool(detect_secrets(text))


def detect_change_set_secrets(change_set: ChangeSet) -> Tuple[SecretLocation, ...]:
    """扫描新增内容和删除旧侧内容，并保留其真实坐标。"""

    locations = []
    for file_change in change_set.files:
        if file_change.is_binary:
            continue
        for hunk in file_change.hunks:
            new_lines = dict(hunk.context_lines)
            new_lines.update(hunk.added_lines)
            for line_number, line_text in sorted(new_lines.items()):
                for match in detect_secrets(line_text):
                    locations.append(
                        SecretLocation(
                            secret_type=match.secret_type,
                            file=file_change.normalized_path,
                            line=line_number,
                            line_side="new",
                            confidence=match.confidence,
                            evidence=redact_text(line_text),
                        )
                    )
            for line_number, line_text in sorted(hunk.deleted_lines.items()):
                for match in detect_secrets(line_text):
                    locations.append(
                        SecretLocation(
                            secret_type=match.secret_type,
                            file=file_change.normalized_path,
                            line=line_number,
                            line_side="old",
                            confidence=match.confidence,
                            evidence=redact_text(line_text),
                        )
                    )

    unique = {}
    for location in locations:
        unique.setdefault(
            (location.file, location.line, location.line_side, location.secret_type),
            location,
        )
    return tuple(
        unique[key]
        for key in sorted(unique, key=lambda item: (item[0], item[2] != "new", item[1], item[3]))
    )
