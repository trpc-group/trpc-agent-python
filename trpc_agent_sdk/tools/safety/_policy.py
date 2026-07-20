"""Policy loading, validation, normalization, and hashing.

The policy is the single source of truth for allow/deny lists, limits, and
per-tool field mappings. Changing YAML is supposed to change behavior
without touching code, so:

* Unknown keys, invalid enum values, or negative limits fail at load time.
* Normalization produces a canonical form used for both matching and the
  policy hash.
* The hash is SHA-256 over canonical JSON so identical configs produce
  identical hashes regardless of YAML formatting.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import PurePosixPath
from typing import Any, Iterable

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from trpc_agent_sdk.tools.safety._exceptions import SafetyPolicyError
from trpc_agent_sdk.tools.safety._models import ScriptLanguage

POLICY_VERSION = "1"


class NetworkPolicy(BaseModel):
    """Allowed domains and IP handling.

    Domains are normalized lowercase. ``"*."`` prefix matches exactly one
    subdomain level (``*.example.com`` matches ``api.example.com`` but not
    ``example.com`` or ``a.b.example.com``).
    """

    model_config = ConfigDict(extra="forbid")

    allow_domains: tuple[str, ...] = ()
    deny_ip_literals: bool = True

    @field_validator("allow_domains", mode="before")
    @classmethod
    def _normalize_domains(cls, value: Any) -> Any:
        if value is None:
            return ()
        if isinstance(value, str):
            raise SafetyPolicyError("allow_domains must be a list")
        normalized: list[str] = []
        for domain in value:
            if not isinstance(domain, str) or not domain.strip():
                raise SafetyPolicyError(f"invalid domain entry: {domain!r}")
            d = domain.strip().lower().rstrip(".")
            if not d:
                raise SafetyPolicyError(f"invalid domain entry: {domain!r}")
            if "*" in d and not d.startswith("*."):
                raise SafetyPolicyError(f"wildcard domain must start with '*.': {domain!r}")
            normalized.append(d)
        return tuple(normalized)


class CommandsPolicy(BaseModel):
    """Executable allow/deny lists (compared by basename)."""

    model_config = ConfigDict(extra="forbid")

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()

    @field_validator("allow", "deny", mode="before")
    @classmethod
    def _normalize_commands(cls, value: Any) -> Any:
        if value is None:
            return ()
        if isinstance(value, str):
            raise SafetyPolicyError("command list must be a list")
        normalized: list[str] = []
        for command in value:
            if not isinstance(command, str) or not command.strip():
                raise SafetyPolicyError(f"invalid command entry: {command!r}")
            normalized.append(command.strip().lower())
        return tuple(normalized)


class PathsPolicy(BaseModel):
    """Denied path globs (matched lexically, no filesystem access)."""

    model_config = ConfigDict(extra="forbid")

    deny: tuple[str, ...] = ()

    @field_validator("deny", mode="before")
    @classmethod
    def _normalize_paths(cls, value: Any) -> Any:
        if value is None:
            return ()
        if isinstance(value, str):
            raise SafetyPolicyError("paths.deny must be a list")
        normalized: list[str] = []
        for path in value:
            if not isinstance(path, str) or not path.strip():
                raise SafetyPolicyError(f"invalid path entry: {path!r}")
            normalized.append(_normalize_path_glob(path.strip()))
        return tuple(normalized)


class LimitsPolicy(BaseModel):
    """Numeric limits enforced by the guard and downstream wrapper."""

    model_config = ConfigDict(extra="forbid")

    max_timeout_seconds: float = 60.0
    max_output_bytes: int = 1_048_576
    max_script_bytes: int = 262_144
    max_sleep_seconds: float = 30.0
    max_parallel_tasks: int = 16
    max_processes: int = 8
    max_file_write_bytes: int = 10_485_760

    @model_validator(mode="after")
    def _check_non_negative(self) -> "LimitsPolicy":
        for field_name in ("max_timeout_seconds", "max_output_bytes", "max_script_bytes", "max_sleep_seconds",
                           "max_parallel_tasks", "max_processes", "max_file_write_bytes"):
            value = getattr(self, field_name)
            if value < 0:
                raise SafetyPolicyError(f"{field_name} must be non-negative")
        return self


class DefaultsPolicy(BaseModel):
    """Knobs that change how ambiguity is treated."""

    model_config = ConfigDict(extra="forbid")

    unknown_construct: str = "needs_human_review"
    guard_error: str = "deny"
    human_review_blocks_execution: bool = True

    @field_validator("unknown_construct", "guard_error")
    @classmethod
    def _validate_decision(cls, value: str) -> str:
        allowed = {"allow", "needs_human_review", "deny"}
        if value not in allowed:
            raise SafetyPolicyError(f"decision must be one of {sorted(allowed)}; got {value!r}")
        return value


class DependenciesPolicy(BaseModel):
    """Decision applied when dependency install commands appear."""

    model_config = ConfigDict(extra="forbid")

    decision: str = "deny"


class ToolFieldMapping(BaseModel):
    """Declarative mapping from tool args to scan fields."""

    model_config = ConfigDict(extra="forbid")

    execution_capable: bool = False
    language: ScriptLanguage = ScriptLanguage.UNKNOWN
    script: str | None = None
    cwd: str | None = None
    env: str | None = None
    timeout: str | None = None
    argv: str | None = None


class ToolSafetyPolicy(BaseModel):
    """Top-level policy document."""

    model_config = ConfigDict(extra="forbid")

    version: str = POLICY_VERSION
    defaults: DefaultsPolicy = Field(default_factory=DefaultsPolicy)
    limits: LimitsPolicy = Field(default_factory=LimitsPolicy)
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    commands: CommandsPolicy = Field(default_factory=CommandsPolicy)
    paths: PathsPolicy = Field(default_factory=PathsPolicy)
    dependencies: DependenciesPolicy = Field(default_factory=DependenciesPolicy)
    tools: dict[str, ToolFieldMapping] = Field(default_factory=dict)
    rule_overrides: dict[str, str] = Field(default_factory=dict)
    audit: "AuditPolicy" = Field(default_factory=lambda: AuditPolicy())
    sensitive_env_key_patterns: tuple[str, ...] = (
        "*KEY*",
        "*TOKEN*",
        "*PASSWORD*",
        "*SECRET*",
        "*CREDENTIAL*",
    )

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if value != POLICY_VERSION:
            raise SafetyPolicyError(f"unsupported policy version {value!r}; expected {POLICY_VERSION!r}")
        return value

    @model_validator(mode="after")
    def _check_rule_overrides(self) -> "ToolSafetyPolicy":
        allowed = {"allow", "needs_human_review", "deny"}
        for rule_id, action in self.rule_overrides.items():
            if action not in allowed:
                raise SafetyPolicyError(f"rule_overrides[{rule_id!r}] must be one of "
                                        f"{sorted(allowed)}; got {action!r}")
        return self

    @property
    def hash(self) -> str:
        """SHA-256 over canonical JSON form."""

        return _compute_policy_hash(self)


class AuditPolicy(BaseModel):
    """Audit sink behavior."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    required: bool = True
    path: str = "tool_safety_audit.jsonl"


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def load_safety_policy(path: str | os.PathLike[str]) -> ToolSafetyPolicy:
    """Load and validate a YAML policy file.

    Raises :class:`SafetyPolicyError` on any malformed input. Never returns
    a partial or permissive policy.
    """

    path = os.fspath(path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise SafetyPolicyError(f"policy file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise SafetyPolicyError(f"invalid YAML in {path}: {exc}") from exc
    if raw is None:
        raise SafetyPolicyError(f"policy file is empty: {path}")
    if not isinstance(raw, dict):
        raise SafetyPolicyError(f"policy root must be a mapping: {path}")
    raw.setdefault("version", POLICY_VERSION)
    try:
        policy = ToolSafetyPolicy.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError
        raise SafetyPolicyError(f"policy validation failed: {exc}") from exc
    return policy


def load_safety_policy_dict(data: dict[str, Any]) -> ToolSafetyPolicy:
    """Load a policy from an in-memory mapping. Used by tests."""

    data = dict(data)
    data.setdefault("version", POLICY_VERSION)
    try:
        return ToolSafetyPolicy.model_validate(data)
    except Exception as exc:
        raise SafetyPolicyError(f"policy validation failed: {exc}") from exc


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #

_TRAILING_SLASH = re.compile(r"/+$")


def _normalize_path_glob(pattern: str) -> str:
    """Lexically normalize a path glob.

    * Expands ``~`` to the literal string ``~`` (no filesystem access).
    * Collapses repeated separators.
    * Strips trailing slashes except for the root ``/``.
    """

    if not pattern:
        return pattern
    if pattern.startswith("~"):
        head = "~"
        rest = pattern[1:]
    else:
        head = ""
        rest = pattern
    rest = rest.replace("\\", "/")
    parts: list[str] = []
    for part in rest.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            continue
        parts.append(part)
    normalized = "/".join(parts)
    if not normalized:
        return head or "."
    if head:
        return f"{head}/{normalized}"
    if pattern.startswith("/"):
        return f"/{normalized}"
    return normalized


def normalize_relpath(path: str) -> str:
    """Best-effort lexical normalization for matching purposes."""

    return _normalize_path_glob(path)


def match_path_glob(path: str, pattern: str) -> bool:
    """Match a path against a glob, lexically.

    ``**`` matches any number of path segments. ``*`` matches within a
    single segment. The pattern also matches when the path lives inside
    a pattern directory (``/etc`` matches ``/etc/passwd``).
    """

    import fnmatch

    if not path or not pattern:
        return False
    norm_path = _normalize_path_glob(path)
    norm_pattern = _normalize_path_glob(pattern)
    if fnmatch.fnmatch(norm_path, norm_pattern):
        return True
    # Also accept ``pattern/**`` style matches: /etc matches /etc/passwd.
    if norm_pattern and not norm_pattern.endswith("**"):
        prefix = norm_pattern.rstrip("/")
        if prefix and (norm_path == prefix or norm_path.startswith(prefix + "/") or norm_path.startswith(prefix + "\\")
                       or (prefix.startswith("~") and norm_path.startswith(prefix + "/"))):
            return True
        # Try with explicit /** suffix.
        if fnmatch.fnmatch(norm_path, f"{norm_pattern}/**"):
            return True
    # Also try matching with leading wildcard for relative paths
    if not norm_path.startswith("/") and not norm_path.startswith("~"):
        for prefix in ("/", "~/"):
            if fnmatch.fnmatch(f"{prefix}{norm_path}", f"{prefix}{norm_pattern}"):
                return True
            if fnmatch.fnmatch(f"{prefix}{norm_path}", f"{prefix}{norm_pattern}/**"):
                return True
    return False


def match_domain(host: str, allowed: Iterable[str]) -> bool:
    """Match a host against the allow list.

    ``*.example.com`` matches exactly ``api.example.com`` (one segment),
    not ``example.com`` and not ``a.b.example.com``.
    """

    if not host:
        return False
    h = host.lower().rstrip(".")
    for entry in allowed:
        e = entry.lower().rstrip(".")
        if e == h:
            return True
        if e.startswith("*."):
            suffix = e[2:]
            if "." in h:
                _, _, host_suffix = h.partition(".")
                if host_suffix == suffix:
                    return True
    return False


def is_sensitive_env_key(key: str, patterns: Iterable[str]) -> bool:
    """Match an env key against sensitive-name patterns.

    Patterns use shell-style ``*`` wildcards (case-insensitive).
    """

    if not key:
        return False
    for pattern in patterns:
        if _shell_match(key.lower(), pattern.lower()):
            return True
    return False


def _shell_match(value: str, pattern: str) -> bool:
    import fnmatch

    return fnmatch.fnmatchcase(value, pattern)


def _compute_policy_hash(policy: ToolSafetyPolicy) -> str:
    canonical = policy.model_dump(mode="json", exclude_none=True)
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# Resolve forward reference for AuditPolicy
ToolSafetyPolicy.model_rebuild()


def normalize_script_path_for_match(path_value: str) -> str:
    """Normalize a script-referenced path for deny-list matching."""

    if not path_value:
        return path_value
    expanded = os.path.expanduser(path_value)
    return str(PurePosixPath(expanded.replace("\\", "/")))
