"""Policy configuration for Script Safety Guard.

Design:
- Built-in default config ensures the guard works out-of-the-box without any external file.
- Users can optionally provide a YAML file to override/append the defaults.
- Merge strategy: lists → append + deduplicate; scalars → override; override:true → full replace.
- Only network.allowed_domains has whitelist-bypass semantics (hit = skip Finding).
  All other fields are rule parameters that rules reference during scanning.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Convention-based auto-discovery
# ---------------------------------------------------------------------------

#: Environment variable to explicitly specify the policy file path.
ENV_POLICY_PATH = "TOOL_SAFETY_POLICY_PATH"

#: Convention file names searched in auto-discovery (priority order).
_CONVENTION_FILENAMES = [
    "tool_safety_policy.yaml",
    "tool_safety_policy.yml",
]

#: Sub-directories searched after CWD root (priority order).
_CONVENTION_SUBDIRS = [
    ".safety",
    "config",
]


# ---------------------------------------------------------------------------
# Sub-policy models
# ---------------------------------------------------------------------------


class NetworkPolicy(BaseModel):
    """Network policy — allowed_domains is the ONLY whitelist-bypass mechanism.

    Domains hitting this list will NOT produce Findings (direct pass-through).
    Supports glob patterns (e.g. '*.github.com').
    """

    allowed_domains: list[str] = Field(
        default_factory=list,
        description="Whitelisted domain patterns (glob supported).",
    )
    override: bool = Field(
        default=False,
        description="If true, user config fully replaces (not appends) the default list.",
    )


class ProcessPolicy(BaseModel):
    """Process policy — rule parameter (NOT whitelist-bypass).

    Commands not in this list will trigger Findings based on rule severity.
    """

    allowed_commands: list[str] = Field(
        default_factory=list,
        description="Commands considered safe for subprocess invocation.",
    )
    override: bool = Field(
        default=False,
        description="If true, user config fully replaces the default list.",
    )


class FileOperationsPolicy(BaseModel):
    """File operations policy — rule parameter (NOT whitelist-bypass).

    Paths in this list will trigger Findings when scripts attempt to access them.
    """

    forbidden_paths: list[str] = Field(
        default_factory=list,
        description="Paths that scripts must not read/write/delete.",
    )
    override: bool = Field(
        default=False,
        description="If true, user config fully replaces the default list.",
    )


class ResourcePolicy(BaseModel):
    """Resource policy — rule parameter (thresholds).

    Exceeding these thresholds will produce Findings.
    """

    max_timeout_seconds: int = Field(
        default=300,
        description="Maximum allowed script execution time in seconds.",
    )
    max_output_size_mb: int = Field(
        default=100,
        description="Maximum allowed output size in megabytes.",
    )


class ReportOutputConfig(BaseModel):
    """Configuration for structured scan report output."""

    enabled: bool = Field(default=True, description="Whether to output report files.")
    dir: str = Field(
        default="./.safety_reports",
        description="Report output directory (relative to CWD or absolute).",
    )
    filename_template: str = Field(
        default="{tool_name}_{timestamp}_report.json",
        description="Filename template. Variables: {tool_name}, {invocation_id}, {timestamp}.",
    )


class AuditOutputConfig(BaseModel):
    """Configuration for audit log (JSONL) output."""

    enabled: bool = Field(default=True, description="Whether to output audit log.")
    file: str = Field(
        default="./.safety_reports/audit.jsonl",
        description="Audit log file path (JSONL format, append mode).",
    )


class OutputConfig(BaseModel):
    """Output configuration — controls report and audit log file writing."""

    report: ReportOutputConfig = Field(default_factory=ReportOutputConfig)
    audit: AuditOutputConfig = Field(default_factory=AuditOutputConfig)


class PolicyConfig(BaseModel):
    """Top-level policy configuration.

    NOTE: Only network.allowed_domains has whitelist-bypass semantics
    (hit = skip Finding). All other fields are rule parameters that
    rules reference during scanning to make severity/decision judgments.
    """

    model_config = {"extra": "ignore"}  # Forward compatibility: ignore unknown fields

    version: str = Field(default="1.0", description="Policy schema version.")
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    process: ProcessPolicy = Field(default_factory=ProcessPolicy)
    file_operations: FileOperationsPolicy = Field(default_factory=FileOperationsPolicy)
    resources: ResourcePolicy = Field(default_factory=ResourcePolicy)
    output: OutputConfig = Field(default_factory=OutputConfig)


# ---------------------------------------------------------------------------
# Auto-discovery logic
# ---------------------------------------------------------------------------


def _auto_discover_policy() -> Optional[Path]:
    """Search for a convention-named policy file in well-known locations.

    Discovery order (first match wins):
    1. Environment variable ``TOOL_SAFETY_POLICY_PATH`` — explicit path.
    2. CWD / <convention filename>
    3. CWD / <subdir> / <convention filename>

    Returns:
        Path to the discovered policy file, or None if not found.
    """
    # Priority 1: Environment variable
    env_path = os.environ.get(ENV_POLICY_PATH)
    if env_path:
        candidate = Path(env_path)
        if candidate.is_file():
            logger.info("Policy discovered via %s: %s", ENV_POLICY_PATH, candidate)
            return candidate
        else:
            logger.warning(
                "%s is set to '%s' but the file does not exist; continuing discovery.",
                ENV_POLICY_PATH,
                env_path,
            )

    # Priority 2: CWD root
    cwd = Path.cwd()
    for name in _CONVENTION_FILENAMES:
        candidate = cwd / name
        if candidate.is_file():
            logger.info("Policy auto-discovered at CWD: %s", candidate)
            return candidate

    # Priority 3: CWD sub-directories
    for subdir in _CONVENTION_SUBDIRS:
        for name in _CONVENTION_FILENAMES:
            candidate = cwd / subdir / name
            if candidate.is_file():
                logger.info("Policy auto-discovered at %s: %s", subdir, candidate)
                return candidate

    return None


# ---------------------------------------------------------------------------
# Built-in default policy
# ---------------------------------------------------------------------------


def _default_policy() -> PolicyConfig:
    """Return the built-in default policy.

    This provides a reasonable baseline for AI/developer workflows.
    No external file is needed — the guard works out-of-the-box.
    """
    return PolicyConfig(
        network=NetworkPolicy(
            allowed_domains=[
                "api.openai.com",
                "*.openai.com",
                "*.googleapis.com",
                "*.anthropic.com",
                "*.githubusercontent.com",
                "github.com",
                "pypi.org",
                "*.python.org",
                "registry.npmjs.org",
                "*.huggingface.co",
            ]
        ),
        process=ProcessPolicy(
            allowed_commands=[
                "python3",
                "python",
                "node",
                "cat",
                "ls",
                "find",
                "grep",
                "echo",
                "head",
                "tail",
                "wc",
                "sort",
                "mkdir",
                "cp",
                "mv",
            ]
        ),
        file_operations=FileOperationsPolicy(
            forbidden_paths=[
                "/etc/",
                "~/.ssh/",
                "~/.aws/",
                "~/.gnupg/",
                "~/.config/",
                "~/.env",
                "/root/",
                "/var/log/",
            ]
        ),
        resources=ResourcePolicy(
            max_timeout_seconds=300,
            max_output_size_mb=100,
        ),
    )


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------


def _merge_list(default_list: list[str], user_list: list[str], override: bool) -> list[str]:
    """Merge two lists: append + deduplicate, or full replace if override=True."""
    if override:
        return list(user_list)
    # Append user items to default, preserving order and deduplicating
    seen = set(default_list)
    merged = list(default_list)
    for item in user_list:
        if item not in seen:
            merged.append(item)
            seen.add(item)
    return merged


def _merge_policies(default: PolicyConfig, user: PolicyConfig) -> PolicyConfig:
    """Merge user policy into default policy.

    - List fields: append + deduplicate (unless override=True in user config)
    - Scalar fields: user value overrides default if explicitly provided
    """
    # Network: merge allowed_domains
    merged_domains = _merge_list(
        default.network.allowed_domains,
        user.network.allowed_domains,
        user.network.override,
    )

    # Process: merge allowed_commands
    merged_commands = _merge_list(
        default.process.allowed_commands,
        user.process.allowed_commands,
        user.process.override,
    )

    # File operations: merge forbidden_paths
    merged_paths = _merge_list(
        default.file_operations.forbidden_paths,
        user.file_operations.forbidden_paths,
        user.file_operations.override,
    )

    # Resources: scalars — user overrides default
    # Only override if user provided non-default values (check against ResourcePolicy defaults)
    resource_defaults = ResourcePolicy()
    max_timeout = (
        user.resources.max_timeout_seconds
        if user.resources.max_timeout_seconds != resource_defaults.max_timeout_seconds
        else default.resources.max_timeout_seconds
    )
    max_output = (
        user.resources.max_output_size_mb
        if user.resources.max_output_size_mb != resource_defaults.max_output_size_mb
        else default.resources.max_output_size_mb
    )

    return PolicyConfig(
        version=user.version if user.version != "1.0" else default.version,
        network=NetworkPolicy(allowed_domains=merged_domains),
        process=ProcessPolicy(allowed_commands=merged_commands),
        file_operations=FileOperationsPolicy(forbidden_paths=merged_paths),
        resources=ResourcePolicy(
            max_timeout_seconds=max_timeout,
            max_output_size_mb=max_output,
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_policy(path: Optional[str | Path] = None) -> PolicyConfig:
    """Load policy configuration.

    Behavior:
    - path=None → attempt auto-discovery of convention-named file;
      if not found, return built-in default policy.
    - path provided but file not found → log warning, return default policy.
    - file found → parse as user config, merge with defaults.
    - parse/validation error → log warning, return default policy.

    Auto-discovery (when path is None):
    1. Environment variable ``TOOL_SAFETY_POLICY_PATH``
    2. CWD/tool_safety_policy.yaml (or .yml)
    3. CWD/.safety/tool_safety_policy.yaml
    4. CWD/config/tool_safety_policy.yaml

    Args:
        path: Optional path to user's YAML policy file.
              If None, auto-discovery is attempted.

    Returns:
        A PolicyConfig instance (always valid, never raises).
    """
    default = _default_policy()

    if path is None:
        # Attempt convention-based auto-discovery
        discovered = _auto_discover_policy()
        if discovered is None:
            return default
        path = discovered

    policy_path = Path(path)
    if not policy_path.exists():
        logger.warning("Policy file not found at '%s', using default policy.", policy_path)
        return default

    try:
        with policy_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.warning(
            "Failed to parse policy file '%s': %s. Using default policy.", policy_path, e
        )
        return default
    except OSError as e:
        logger.warning(
            "Failed to read policy file '%s': %s. Using default policy.", policy_path, e
        )
        return default

    if not isinstance(data, dict):
        logger.warning(
            "Policy file '%s' does not contain a mapping. Using default policy.", policy_path
        )
        return default

    try:
        user_policy = PolicyConfig(**data)
    except Exception as e:
        logger.warning(
            "Failed to validate policy file '%s': %s. Using default policy.", policy_path, e
        )
        return default

    return _merge_policies(default, user_policy)
