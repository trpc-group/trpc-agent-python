"""tRPC Agent tool Filter backed by the deterministic command policy."""

import os
import uuid
from datetime import datetime
from datetime import timezone
from typing import Any

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.filter import BaseFilter

from reports.models import FilterDecision

from .policy import CommandPolicy
from .policy import ReviewPolicyContext
from .policy import SandboxCommand

FILTER_DECISIONS_METADATA_KEY = "code_review_filter_decisions"
_ALLOWED_ARGUMENTS = frozenset(
    {
        "skill",
        "command",
        "timeout",
        "env",
        "cwd",
        "stdin",
        "editor_text",
        "output_files",
        "inputs",
        "outputs",
        "save_as_artifacts",
        "omit_inline_content",
        "artifact_prefix",
        # These two fields are accepted by the Filter contract even though the
        # current SDK skill_run schema does not expose them to the model.
        "max_output_bytes",
        "network_required",
    }
)


class SandboxToolFilter(BaseFilter):
    """Block unsafe ``skill_run`` commands before sandbox execution."""

    def __init__(
        self,
        policy: CommandPolicy | None = None,
        context: ReviewPolicyContext | None = None,
        max_sandbox_runs: int | None = None,
    ) -> None:
        super().__init__()
        self.policy = policy or CommandPolicy.from_env(context)
        configured_limit = max_sandbox_runs
        if configured_limit is None:
            configured_limit = int(os.getenv("CODE_REVIEW_MAX_SANDBOX_RUNS", "12"))
        if not 1 <= configured_limit <= 12:
            raise ValueError(
                "CODE_REVIEW_MAX_SANDBOX_RUNS must be between 1 and 12"
            )
        self.max_sandbox_runs = configured_limit
        self._sandbox_run_attempts = 0

    async def _before(
        self,
        ctx: AgentContext,
        req: Any,
        rsp: FilterResult,
    ) -> None:
        args = req if isinstance(req, dict) else {}
        command = str(args.get("command", ""))[:4096]
        self._sandbox_run_attempts += 1
        if self._sandbox_run_attempts > self.max_sandbox_runs:
            decision = FilterDecision(
                decision_id=str(uuid.uuid4()),
                command=command,
                decision="deny",
                reason="review sandbox-run budget exhausted",
                created_at=datetime.now(timezone.utc),
            )
        else:
            try:
                if not isinstance(req, dict):
                    raise ValueError("sandbox request must be an object")
                unknown = set(args) - _ALLOWED_ARGUMENTS
                if unknown:
                    raise ValueError("sandbox request contains unsupported fields")
                if args.get("skill") != "code-review":
                    raise ValueError("sandbox request must target the code-review Skill")
                restricted_fields = (
                    "cwd",
                    "stdin",
                    "editor_text",
                    "output_files",
                    "inputs",
                    "outputs",
                    "save_as_artifacts",
                    "omit_inline_content",
                    "artifact_prefix",
                )
                if any(bool(args.get(name)) for name in restricted_fields):
                    raise ValueError(
                        "sandbox request contains unsupported staging or output options"
                    )
                request = SandboxCommand(
                    command=command,
                    timeout_seconds=float(args.get("timeout") or 30.0),
                    max_output_bytes=int(
                        args.get("max_output_bytes") or self.policy.max_output_bytes
                    ),
                    environment=args.get("env") or {},
                    network_required=bool(args.get("network_required", False)),
                )
            except (TypeError, ValueError):
                decision = FilterDecision(
                    decision_id=str(uuid.uuid4()),
                    command=command,
                    decision="deny",
                    reason="sandbox request contains invalid resource or environment parameters",
                    created_at=datetime.now(timezone.utc),
                )
            else:
                decision = self.policy.evaluate(request)
        decisions = list(ctx.get_metadata(FILTER_DECISIONS_METADATA_KEY, []))
        decisions.append(decision.model_dump(mode="json"))
        ctx.with_metadata(FILTER_DECISIONS_METADATA_KEY, decisions)
        if decision.decision != "allow":
            rsp.error = PermissionError(decision.reason)
            rsp.is_continue = False
