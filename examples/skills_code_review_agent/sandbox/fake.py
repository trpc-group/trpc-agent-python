"""Non-executing sandbox simulator for deterministic local tests."""

import time
import uuid

from filters.policy import CommandPolicy
from filters.policy import SandboxCommand
from inputs.models import ParsedReviewInput
from reports.models import FilterDecision
from reports.models import SandboxRun
from security import redact_text


class FakeSandbox:
    """Exercise policy and run-record handling without executing host code."""

    def __init__(self, policy: CommandPolicy | None = None) -> None:
        self.policy = policy or CommandPolicy.from_env()

    def run(
        self,
        request: SandboxCommand,
        parsed_input: ParsedReviewInput,
    ) -> tuple[FilterDecision, SandboxRun]:
        """Return a simulated result after applying the real command policy."""
        started = time.perf_counter()
        decision = self.policy.evaluate(request)
        run_id = str(uuid.uuid4())
        duration_ms = (time.perf_counter() - started) * 1000

        if decision.decision != "allow":
            return decision, SandboxRun(
                run_id=run_id,
                command=request.command,
                status="blocked",
                duration_ms=duration_ms,
                stderr_summary=decision.reason,
                error_type="FilterBlocked",
            )

        if "SANDBOX_TIMEOUT" in parsed_input.diff_text:
            return decision, SandboxRun(
                run_id=run_id,
                command=request.command,
                status="timeout",
                duration_ms=request.timeout_seconds * 1000,
                timed_out=True,
                stderr_summary="simulated sandbox timeout",
                error_type="TimeoutError",
            )
        if "SANDBOX_FAIL" in parsed_input.diff_text:
            return decision, SandboxRun(
                run_id=run_id,
                command=request.command,
                status="failed",
                duration_ms=duration_ms,
                exit_code=1,
                stderr_summary="simulated sandbox failure",
                error_type="SandboxExecutionError",
            )

        return decision, SandboxRun(
            run_id=run_id,
            command=request.command,
            status="simulated",
            duration_ms=duration_ms,
            exit_code=0,
            stdout_summary=redact_text("fake sandbox validation completed"),
        )
