#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Pre-execution Filter policy for code review sandbox runs."""

from __future__ import annotations

import posixpath
import re
import shlex
from collections.abc import Callable
from typing import Any

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter

from .core import FilterDecision

_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_SHELL_META = re.compile(r"[;&|`$<>\n\r]")
_HIGH_RISK_COMMANDS = frozenset({
    "bash",
    "chmod",
    "chown",
    "curl",
    "dd",
    "docker",
    "mkfs",
    "mount",
    "nc",
    "netcat",
    "rm",
    "sh",
    "sudo",
    "wget",
})
_FORBIDDEN_PATH_PREFIXES = (
    "/boot",
    "/dev",
    "/etc",
    "/proc",
    "/root",
    "/sys",
    "/var/run",
)


class ReviewExecutionFilter(BaseFilter):
    """Block unsafe or over-budget Skill runs before workspace creation."""

    def __init__(
        self,
        *,
        allowed_scripts: set[str],
        env_allowlist: set[str],
        network_allowlist: set[str],
        max_timeout_seconds: int,
        max_output_bytes: int,
        decision_sink: Callable[[FilterDecision], None],
    ) -> None:
        super().__init__()
        self.name = "code_review_execution_policy"
        self._allowed_scripts = {self._normalize_script(item) for item in allowed_scripts}
        self._env_allowlist = env_allowlist
        self._network_allowlist = network_allowlist
        self._max_timeout_seconds = max_timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._decision_sink = decision_sink

    async def _before(self, ctx: AgentContext, req: Any, rsp: FilterResult) -> None:
        """Evaluate command, path, network, environment, and budget policy."""

        decision = self.evaluate(req)
        self._decision_sink(decision)
        if decision.decision == "allow":
            return
        rsp.rsp = {
            "filter_blocked": True,
            "decision": decision.model_dump(),
        }
        rsp.is_continue = False

    def evaluate(self, request: Any) -> FilterDecision:
        """Return a deterministic policy decision."""

        if not isinstance(request, dict):
            return self._decision("deny", "invalid_request", "Skill request must be an object.", "")

        command = str(request.get("command") or "").strip()
        script = self._extract_script(command)
        if not command or not script:
            return self._decision(
                "deny",
                "invalid_command",
                "Only a single python3 script command is allowed.",
                script,
            )
        if _SHELL_META.search(command):
            return self._decision(
                "deny",
                "shell_meta",
                "Shell operators and variable expansion are prohibited.",
                script,
            )
        try:
            argv = shlex.split(command)
        except ValueError:
            return self._decision("deny", "invalid_command", "Command quoting is invalid.", script)
        executable = posixpath.basename(argv[0]) if argv else ""
        if executable in _HIGH_RISK_COMMANDS or executable not in {"python", "python3"}:
            return self._decision(
                "deny",
                "high_risk_command",
                f"Executable {executable!r} is not allowed.",
                script,
            )

        normalized_script = self._normalize_script(script)
        if (script.startswith("/") or normalized_script.startswith("../")
                or normalized_script not in self._allowed_scripts):
            return self._decision(
                "deny",
                "script_not_allowed",
                f"Script {script!r} is outside the Skill allowlist.",
                script,
            )

        for token in argv[2:]:
            if token.startswith(_FORBIDDEN_PATH_PREFIXES):
                return self._decision(
                    "deny",
                    "forbidden_path",
                    f"Access to path {token!r} is prohibited.",
                    script,
                )

        requested_hosts = request.get("network_hosts") or []
        if not isinstance(requested_hosts, list):
            requested_hosts = [str(requested_hosts)]
        denied_hosts = sorted(set(map(str, requested_hosts)) - self._network_allowlist)
        if denied_hosts:
            return self._decision(
                "deny",
                "network_not_allowed",
                f"Network hosts are not allowlisted: {', '.join(denied_hosts)}.",
                script,
                requested_hosts,
            )

        env = request.get("env") or {}
        if not isinstance(env, dict):
            return self._decision("deny", "invalid_environment", "Environment must be an object.", script)
        invalid_env = sorted(
            key for key in map(str, env)
            if not _ENV_NAME.fullmatch(key) or key not in self._env_allowlist)
        if invalid_env:
            return self._decision(
                "deny",
                "environment_not_allowed",
                f"Environment keys are not allowlisted: {', '.join(invalid_env)}.",
                script,
            )

        timeout = int(request.get("timeout") or 0)
        if timeout <= 0 or timeout > self._max_timeout_seconds:
            return self._decision(
                "needs_human_review",
                "timeout_budget",
                f"Timeout {timeout}s exceeds the 1-{self._max_timeout_seconds}s policy.",
                script,
            )

        outputs = request.get("outputs") or {}
        if isinstance(outputs, dict):
            max_total = int(outputs.get("max_total_bytes") or 0)
            max_file = int(outputs.get("max_file_bytes") or 0)
            if (max_total <= 0 or max_file <= 0 or max_total > self._max_output_bytes
                    or max_file > self._max_output_bytes):
                return self._decision(
                    "needs_human_review",
                    "output_budget",
                    f"Output budget exceeds {self._max_output_bytes} bytes.",
                    script,
                )

        return self._decision("allow", "policy_allow", "Execution satisfies the sandbox policy.", script,
                              requested_hosts)

    @staticmethod
    def _normalize_script(script: str) -> str:
        return posixpath.normpath(script.strip().replace("\\", "/"))

    @staticmethod
    def _extract_script(command: str) -> str:
        try:
            argv = shlex.split(command)
        except ValueError:
            return ""
        if len(argv) < 2:
            return ""
        return argv[1]

    @staticmethod
    def _decision(
        decision: str,
        rule_id: str,
        reason: str,
        script: str,
        network_hosts: list[str] | None = None,
    ) -> FilterDecision:
        return FilterDecision(
            decision=decision,
            rule_id=rule_id,
            reason=reason,
            script=script,
            network_hosts=network_hosts or [],
        )
