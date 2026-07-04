# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool script safety guard primitives."""

from ._audit import SafetyAuditLogger
from ._audit import build_safety_audit_event
from ._audit import set_safety_span_attributes
from ._code_executor import SafetyGuardedCodeExecutor
from ._filter import ToolSafetyFilter
from ._policy import DEFAULT_DENIED_PATHS
from ._policy import DEFAULT_SENSITIVE_ENV_KEYS
from ._policy import RulePolicy
from ._policy import SafetyPolicy
from ._policy import default_safety_policy
from ._policy import load_safety_policy
from ._policy import resolve_safety_policy
from ._matchers import get_command_name
from ._matchers import is_command_allowed
from ._matchers import is_command_denied
from ._matchers import is_domain_allowed
from ._matchers import is_env_key_sensitive
from ._matchers import is_path_denied
from ._matchers import matches_any_pattern
from ._redaction import REDACTION_MARKER
from ._redaction import contains_secret
from ._redaction import redact_env
from ._redaction import redact_evidence
from ._redaction import redact_text
from ._rules import DEFAULT_RULE_DEFINITIONS
from ._rules import RuleDefinition
from ._rules import apply_rule_policy
from ._rules import get_rule_definition
from ._rules import is_rule_enabled
from ._rules import iter_rule_definitions
from ._rules import make_finding
from ._rules import merge_findings
from ._rules import should_block_decision
from ._scanner import SafetyScanner
from ._types import RiskLevel
from ._types import RiskType
from ._types import SafetyAuditEvent
from ._types import SafetyDecision
from ._types import SafetyReport
from ._types import ScanFinding
from ._types import ScanTarget
from ._types import ScriptLanguage

__all__ = [
    "DEFAULT_DENIED_PATHS",
    "DEFAULT_RULE_DEFINITIONS",
    "DEFAULT_SENSITIVE_ENV_KEYS",
    "REDACTION_MARKER",
    "RiskLevel",
    "RiskType",
    "SafetyAuditLogger",
    "RuleDefinition",
    "RulePolicy",
    "SafetyAuditEvent",
    "SafetyDecision",
    "SafetyGuardedCodeExecutor",
    "SafetyPolicy",
    "SafetyReport",
    "SafetyScanner",
    "ScanFinding",
    "ScanTarget",
    "ScriptLanguage",
    "ToolSafetyFilter",
    "apply_rule_policy",
    "build_safety_audit_event",
    "contains_secret",
    "default_safety_policy",
    "get_command_name",
    "get_rule_definition",
    "is_command_allowed",
    "is_command_denied",
    "is_domain_allowed",
    "is_env_key_sensitive",
    "is_path_denied",
    "is_rule_enabled",
    "iter_rule_definitions",
    "load_safety_policy",
    "make_finding",
    "matches_any_pattern",
    "merge_findings",
    "redact_env",
    "redact_evidence",
    "redact_text",
    "resolve_safety_policy",
    "set_safety_span_attributes",
    "should_block_decision",
]
