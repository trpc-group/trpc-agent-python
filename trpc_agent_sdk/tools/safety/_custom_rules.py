# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Lightweight custom safety rule registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ._policy import ToolSafetyPolicy
from ._types import RiskFinding


@dataclass(frozen=True)
class SafetyRuleContext:
    """Context passed to a custom safety rule."""

    script: str
    language: str
    policy: ToolSafetyPolicy
    command_args: list[str]
    cwd: str
    env: dict[str, str]
    tool_name: str
    tool_metadata: dict


SafetyRule = Callable[[SafetyRuleContext], list[RiskFinding]]


@dataclass(frozen=True)
class RegisteredSafetyRule:
    """Registered custom rule metadata."""

    name: str
    rule: SafetyRule
    languages: frozenset[str] | None


_CUSTOM_RULES: dict[str, RegisteredSafetyRule] = {}


def register_safety_rule(
    name: str,
    rule: SafetyRule,
    languages: list[str] | set[str] | tuple[str, ...] | None = None,
) -> None:
    """Register a deterministic in-process custom safety rule."""
    normalized = _normalize_name(name)
    if not callable(rule):
        raise TypeError("safety rule must be callable")
    language_set = None
    if languages is not None:
        language_set = frozenset(_normalize_language(language) for language in languages)
    _CUSTOM_RULES[normalized] = RegisteredSafetyRule(normalized, rule, language_set)


def unregister_safety_rule(name: str) -> None:
    """Unregister a custom safety rule by name."""
    _CUSTOM_RULES.pop(_normalize_name(name), None)


def clear_custom_safety_rules() -> None:
    """Remove all registered custom safety rules."""
    _CUSTOM_RULES.clear()


def iter_custom_safety_rules(language: str):
    """Yield custom safety rules that apply to the normalized language."""
    normalized_language = _normalize_language(language)
    for registered in list(_CUSTOM_RULES.values()):
        if registered.languages is None or normalized_language in registered.languages:
            yield registered


def _normalize_name(name: str) -> str:
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("safety rule name must be non-empty")
    return normalized


def _normalize_language(language: str) -> str:
    normalized = str(language or "unknown").strip().lower()
    if normalized in {"py", "python3"}:
        return "python"
    if normalized in {"sh", "shell", "zsh", "ksh"}:
        return "bash"
    if normalized in {"python", "bash"}:
        return normalized
    return "unknown"
