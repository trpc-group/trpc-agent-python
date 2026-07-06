# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Filter governance: pre-execution policy gate for sandbox runs.

Implements issue requirement 8 with the SDK's real filter chain
(``trpc_agent_sdk.filter.BaseFilter`` + ``run_filters``): every sandbox run is
wrapped as a request; the filter's ``_before`` may veto it, in which case the
terminal handler (the actual sandbox execution) is NEVER invoked and the
policy decision is returned instead.

Blocking mechanics (verified against ``filter/_base_filter.py``): a policy
block sets ``rsp.rsp = PolicyDecision(...)`` and ``rsp.is_continue = False``.
It must NOT set ``rsp.error`` — ``run_filters`` re-raises errors, and a policy
veto is a decision, not a failure.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import List
from typing import Optional
from typing import Union

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import run_filters

from .config import PolicyConfig

ACTION_ALLOW = "allow"
ACTION_DENY = "deny"
ACTION_NEEDS_HUMAN_REVIEW = "needs_human_review"

#: Script-content patterns that mark a script as high-risk. High-risk scripts
#: are not silently executed OR silently dropped — they go to a human.
DANGEROUS_PATTERNS = (
    ("rm_rf", re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\b")),
    ("shutil_rmtree_root", re.compile(r"shutil\.rmtree\s*\(\s*[\"']/")),
    ("network_fetch", re.compile(r"\b(?:curl|wget)\b|\burllib\.request\b|\brequests\.(?:get|post)\b"
                                 r"|http[s]?://(?!localhost|127\.0\.0\.1)")),
    ("raw_socket", re.compile(r"\bsocket\.socket\b")),
    ("privilege", re.compile(r"\bsudo\b|\bsetuid\b|\bos\.setuid\b")),
    ("world_writable", re.compile(r"\bchmod\s+777\b|\bos\.chmod\s*\([^)]*0o?777")),
    ("sensitive_file", re.compile(r"/etc/passwd|/etc/shadow|\.aws/credentials|id_rsa")),
    ("shell_out_network", re.compile(r"subprocess\.[a-zA-Z_]+\s*\([^)]*(?:curl|wget|nc|ncat|ssh)\b")),
)


@dataclass
class SandboxRunRequest:
    """Everything the policy needs to know about one intended sandbox run."""

    kind: str  # parse_diff | static_checks | custom
    cmd: str
    args: List[str] = field(default_factory=list)
    script_host_path: str = ""  # host path of the script, for content inspection
    wants_network: bool = False
    est_timeout: float = 30.0
    run_index: int = 0
    total_sandbox_seconds: float = 0.0


@dataclass
class PolicyDecision:
    """Outcome of the governance gate for one sandbox run."""

    action: str  # allow | deny | needs_human_review
    reasons: List[str] = field(default_factory=list)
    rule: str = ""

    @property
    def blocked(self) -> bool:
        return self.action != ACTION_ALLOW


class SandboxGovernanceFilter(BaseFilter):
    """Pre-execution policy checks for sandbox runs (deny / needs_human_review).

    Check order (first hit wins):
      1. command not whitelisted            → deny
      2. risky script content               → needs_human_review
      3. forbidden path in args             → deny
      4. non-whitelisted network access     → deny
      5. over budget (runs / total seconds) → needs_human_review
    """

    def __init__(self,
                 policy: PolicyConfig,
                 on_decision: Optional[Callable[[SandboxRunRequest, PolicyDecision], None]] = None) -> None:
        super().__init__()
        self._policy = policy
        self._on_decision = on_decision

    # -- individual checks -------------------------------------------------

    def _check_cmd(self, req: SandboxRunRequest) -> Optional[PolicyDecision]:
        # The request carries the real interpreter (may be an absolute
        # sys.executable path, or foo.exe on Windows) — match the policy list
        # against the exact value and its normalized basename.
        cmd_name = os.path.basename(req.cmd)
        if cmd_name.lower().endswith(".exe"):
            cmd_name = cmd_name[:-len(".exe")]
        if req.cmd not in self._policy.allowed_cmds and cmd_name not in self._policy.allowed_cmds:
            return PolicyDecision(
                action=ACTION_DENY,
                reasons=[f"command {req.cmd!r} is not in the allowed command list "
                         f"{list(self._policy.allowed_cmds)}"],
                rule="allowed_cmds",
            )
        return None

    def _check_script_content(self, req: SandboxRunRequest) -> Optional[PolicyDecision]:
        if not req.script_host_path or not os.path.isfile(req.script_host_path):
            return None
        try:
            with open(req.script_host_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
        except OSError:
            return PolicyDecision(action=ACTION_DENY,
                                  reasons=[f"script {req.script_host_path!r} is unreadable"],
                                  rule="risky_script")
        hits = [name for name, pattern in DANGEROUS_PATTERNS if pattern.search(content)]
        if hits:
            return PolicyDecision(
                action=ACTION_NEEDS_HUMAN_REVIEW,
                reasons=[f"script matches high-risk pattern(s): {', '.join(hits)}"],
                rule="risky_script",
            )
        return None

    def _check_paths(self, req: SandboxRunRequest) -> Optional[PolicyDecision]:
        for arg in req.args:
            if arg.startswith("-"):
                continue
            normalized = os.path.normpath(arg)
            for forbidden in self._policy.forbidden_paths:
                if (normalized == forbidden or normalized.startswith(forbidden.rstrip("/") + "/")
                        or forbidden in normalized.split(os.sep) or normalized.startswith("~")):
                    return PolicyDecision(
                        action=ACTION_DENY,
                        reasons=[f"argument {arg!r} touches forbidden path pattern {forbidden!r}"],
                        rule="forbidden_paths",
                    )
        return None

    def _check_network(self, req: SandboxRunRequest) -> Optional[PolicyDecision]:
        if req.wants_network and not self._policy.allow_network:
            return PolicyDecision(
                action=ACTION_DENY,
                reasons=["run requests network access but the policy network whitelist is empty"],
                rule="network_whitelist",
            )
        return None

    def _check_budget(self, req: SandboxRunRequest) -> Optional[PolicyDecision]:
        if req.run_index >= self._policy.max_sandbox_runs:
            return PolicyDecision(
                action=ACTION_NEEDS_HUMAN_REVIEW,
                reasons=[f"sandbox run budget exhausted "
                         f"({req.run_index} >= max {self._policy.max_sandbox_runs} runs)"],
                rule="run_budget",
            )
        if req.total_sandbox_seconds + req.est_timeout > self._policy.max_total_sandbox_seconds:
            return PolicyDecision(
                action=ACTION_NEEDS_HUMAN_REVIEW,
                reasons=[f"sandbox time budget exceeded "
                         f"({req.total_sandbox_seconds:.1f}s used + {req.est_timeout:.1f}s requested "
                         f"> max {self._policy.max_total_sandbox_seconds:.1f}s)"],
                rule="time_budget",
            )
        return None

    # -- filter hook --------------------------------------------------------

    async def _before(self, ctx: AgentContext, req: SandboxRunRequest, rsp: FilterResult) -> None:
        for check in (self._check_cmd, self._check_script_content, self._check_paths,
                      self._check_network, self._check_budget):
            decision = check(req)
            if decision is not None:
                # Policy veto: set the decision as the response and stop the
                # chain. NEVER set rsp.error — run_filters would raise.
                rsp.rsp = decision
                rsp.is_continue = False
                if self._on_decision:
                    self._on_decision(req, decision)
                return
        if self._on_decision:
            self._on_decision(req, PolicyDecision(action=ACTION_ALLOW, reasons=[], rule=""))


async def gated_sandbox_run(
    req: SandboxRunRequest,
    governance: SandboxGovernanceFilter,
    handler: Callable[[], Awaitable[Any]],
    ctx: Optional[AgentContext] = None,
) -> Union[Any, PolicyDecision]:
    """Run ``handler`` behind the governance filter chain.

    Returns the handler result when allowed, or the blocking
    :class:`PolicyDecision` when vetoed (handler is then never called).
    """
    ctx = ctx or create_agent_context()
    return await run_filters(ctx, req, [governance], handler)
