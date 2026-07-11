# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Rule-id constants and default risk/decision metadata.

Scanners reference these constants; load_policy() reads DEFAULT_RULE_POLICIES
so every rule_id has a well-defined default even without YAML overrides.
"""
from __future__ import annotations

from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import RiskLevel

# --- rule_id constants (prefix style mirrors trpc-agent-go/tool/safety) ---
# Code execution
R_CODE_UNSAFE_EVAL = "tool-code-unsafe-eval"
R_CODE_UNSAFE_EXEC = "tool-code-unsafe-exec"
R_CODE_UNSAFE_IMPORT = "tool-code-unsafe-import"
# Dangerous filesystem
R_FS_RECURSIVE_DELETE = "tool-fs-recursive-delete"
R_FS_READ_CREDENTIALS = "tool-fs-read-credentials"
R_FS_SYSTEM_DIR = "tool-fs-system-dir-write"
# Network egress
R_NET_HTTP = "tool-net-http"
R_NET_SOCKET = "tool-net-socket"
# Process / system command
R_PROC_SUBPROCESS = "tool-proc-subprocess"
R_PROC_SHELL_PIPE = "tool-proc-shell-pipe"
R_PROC_PRIVILEGE_ESCALATION = "tool-proc-privilege-escalation"
# Dependency install
R_PKG_INSTALL = "tool-pkg-install"
# Resource abuse
R_RES_INFINITE_LOOP = "tool-res-infinite-loop"
R_RES_FORK_BOMB = "tool-res-fork-bomb"
R_RES_LONG_SLEEP = "tool-res-long-sleep"
R_RES_LARGE_WRITE = "tool-res-large-write"
R_RES_CONCURRENT_FLOOD = "tool-res-concurrent-flood"
# Secret leakage
R_SECRET_LOGGING = "tool-secret-logging"
R_SECRET_PRIVATE_KEY = "tool-secret-private-key"

# rule_id -> (default RiskLevel, default Decision). Decision is never UNDECIDED
# here: every rule commits to a recommendation; the aggregator still applies
# policy thresholds as a fallback.
DEFAULT_RULE_POLICIES: dict[str, tuple[RiskLevel, Decision]] = {
    R_CODE_UNSAFE_EVAL: (RiskLevel.HIGH, Decision.DENY),
    R_CODE_UNSAFE_EXEC: (RiskLevel.HIGH, Decision.DENY),
    R_CODE_UNSAFE_IMPORT: (RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
    R_FS_RECURSIVE_DELETE: (RiskLevel.HIGH, Decision.DENY),
    R_FS_READ_CREDENTIALS: (RiskLevel.HIGH, Decision.DENY),
    R_FS_SYSTEM_DIR: (RiskLevel.HIGH, Decision.DENY),
    R_NET_HTTP: (RiskLevel.HIGH, Decision.DENY),
    R_NET_SOCKET: (RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
    R_PROC_SUBPROCESS: (RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
    R_PROC_SHELL_PIPE: (RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
    R_PROC_PRIVILEGE_ESCALATION: (RiskLevel.HIGH, Decision.DENY),
    R_PKG_INSTALL: (RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
    R_RES_INFINITE_LOOP: (RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
    R_RES_FORK_BOMB: (RiskLevel.HIGH, Decision.DENY),
    R_RES_LONG_SLEEP: (RiskLevel.LOW, Decision.NEEDS_REVIEW),
    R_RES_LARGE_WRITE: (RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
    R_RES_CONCURRENT_FLOOD: (RiskLevel.MEDIUM, Decision.NEEDS_REVIEW),
    R_SECRET_LOGGING: (RiskLevel.HIGH, Decision.DENY),
    R_SECRET_PRIVATE_KEY: (RiskLevel.HIGH, Decision.DENY),
}
