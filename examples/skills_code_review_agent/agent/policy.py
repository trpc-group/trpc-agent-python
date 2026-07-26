"""Secret redaction and fail-closed execution-plan filtering."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Awaitable
from collections.abc import Callable
from datetime import datetime
from datetime import timezone
from pathlib import PurePosixPath
from pathlib import PureWindowsPath
from typing import Any

from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.abc import FilterType
from trpc_agent_sdk.filter import BaseFilter

from .constants import ALLOWED_ENV_NAMES
from .constants import ALLOWED_INTERPRETERS
from .constants import BLOCKED_PATH_PARTS
from .constants import LOCAL_RUNTIME_OPT_IN_ENV
from .constants import MAX_JSON_DEPTH
from .constants import MAX_OUTPUT_BYTES
from .constants import REDACTION_MARKER
from .constants import SANDBOX_TIMEOUT_SECONDS
from .models import DecisionAction
from .models import ExecutionPlan
from .models import FilterDecision

AuditCallback = Callable[[list[FilterDecision]], Awaitable[None]]

_FIXED_SCRIPT = "scripts/scan_rules.py"
_FIXED_INPUT = "../../work/inputs/review_input.json"
_FIXED_OUTPUT = "../../out/findings.jsonl"
_FIXED_CWD = "skills/code-review"
FIXED_SCAN_ARGV = (
    "python",
    _FIXED_SCRIPT,
    "--input",
    _FIXED_INPUT,
    "--output",
    _FIXED_OUTPUT,
)
_SUPPORTED_RUNTIMES = frozenset({"container", "local"})
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    re.DOTALL,
)
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?P<name>[\"']?(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|密码|密钥|令牌)[\"']?)"
    r"(?P<separator>\s*[:=]\s*)(?:[\"'][^\"'\r\n]{8,}[\"']|[^\"'\s,;}]{8,})", )
_SPLIT_ASSIGNMENT_PATTERN = re.compile(
    r"(?is)(?P<prefix>[\"']?(?:api[_-]?key|access[_-]?token|client[_-]?secret|password"
    r"|密码|密钥|令牌)[\"']?"
    r"\s*[:=]\s*\(?)(?:\s*[\"'][^\"']+[\"']\s*\+?){2,}", )
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_URL_CREDENTIAL_PATTERN = re.compile(r"(https?://[^/\s:@]+):([^@\s/]+)@")
_TOKEN_PATTERN = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|gh[pousr]_[A-Za-z0-9]{30,}|"
    r"sk-[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b", )
_SENSITIVE_NAME_PATTERN = re.compile(
    r"(?i)api[_-]?key|access[_-]?token|client[_-]?secret|password|authorization|密码|密钥|令牌", )


class SecretRedactor:
    """Redact credentials before text leaves the review process."""

    def redact_text(self, value: str) -> str:
        """Redact supported secret forms from text."""
        redacted = _PRIVATE_KEY_PATTERN.sub(REDACTION_MARKER, value)
        redacted = _SPLIT_ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group('prefix')}{REDACTION_MARKER}",
            redacted,
        )
        redacted = _ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group('name')}{match.group('separator')}{REDACTION_MARKER}",
            redacted,
        )
        redacted = _BEARER_PATTERN.sub(f"Bearer {REDACTION_MARKER}", redacted)
        redacted = _URL_CREDENTIAL_PATTERN.sub(
            lambda match: f"{match.group(1)}:{REDACTION_MARKER}@",
            redacted,
        )
        return _TOKEN_PATTERN.sub(REDACTION_MARKER, redacted)

    def redact_value(self, value: Any, depth: int = 0) -> Any:
        """Recursively redact JSON-like values with a depth cap."""
        if depth > MAX_JSON_DEPTH:
            return REDACTION_MARKER
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            return {
                key: (REDACTION_MARKER if _SENSITIVE_NAME_PATTERN.fullmatch(str(key)) else self.redact_value(
                    item, depth + 1))
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.redact_value(item, depth + 1) for item in value]
        return value


def _plan_payload(plan: ExecutionPlan) -> dict[str, Any]:
    payload = plan.model_dump(mode="json")
    payload.pop("digest", None)
    return payload


def calculate_plan_digest(plan: ExecutionPlan) -> str:
    """Hash every execution-relevant plan field."""
    encoded = json.dumps(
        _plan_payload(plan),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_execution_plan(
    runtime: str,
    input_digest: str,
    skill_digest: str,
) -> ExecutionPlan:
    """Build the only execution plan accepted by the example."""
    draft = ExecutionPlan(
        argv=FIXED_SCAN_ARGV,
        cwd=_FIXED_CWD,
        input_path="work/inputs/review_input.json",
        output_path="out/findings.jsonl",
        environment=(
            ("PYTHONIOENCODING", "utf-8"),
            ("PYTHONUTF8", "1"),
        ),
        runtime=runtime,
        network_allowed=False,
        timeout_seconds=SANDBOX_TIMEOUT_SECONDS,
        output_limit_bytes=MAX_OUTPUT_BYTES,
        input_digest=input_digest,
        skill_digest=skill_digest,
        digest="pending",
    )
    return draft.model_copy(update={"digest": calculate_plan_digest(draft)})


def _decision(
    action: DecisionAction,
    rule_id: str,
    reason: str,
    plan: ExecutionPlan,
) -> FilterDecision:
    return FilterDecision(
        action=action,
        rule_id=rule_id,
        reason=reason,
        plan_digest=plan.digest,
        created_at=datetime.now(timezone.utc),
    )


def _is_safe_relative_path(value: str) -> bool:
    normalized = value.replace("\\", "/")
    posix_path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    lowered = {part.casefold() for part in posix_path.parts}
    return bool(normalized and "\x00" not in normalized and not posix_path.is_absolute()
                and not windows_path.is_absolute() and not windows_path.drive and ".." not in posix_path.parts
                and not lowered.intersection(BLOCKED_PATH_PARTS))


class ReviewPolicyFilter(BaseFilter):
    """Framework-compatible Filter that audits before allowing execution."""

    def __init__(self, audit: AuditCallback) -> None:
        super().__init__()
        self.name = "code_review_execution_plan"
        self.type = FilterType.TOOL
        self._audit = audit

    def evaluate(self, plan: ExecutionPlan) -> list[FilterDecision]:
        """Evaluate all fail-closed policy rules."""
        checks = (
            self._check_integrity(plan),
            self._check_command(plan),
            self._check_paths(plan),
            self._check_environment(plan),
            self._check_runtime(plan),
            self._check_budget(plan),
        )
        return list(checks)

    async def evaluate_and_audit(self, plan: ExecutionPlan) -> list[FilterDecision]:
        """Persist decisions before the caller may execute."""
        try:
            decisions = self.evaluate(plan)
        except Exception as exc:  # pylint: disable=broad-except
            decisions = [
                _decision(
                    DecisionAction.DENY,
                    "filter.internal-error",
                    f"filter failed closed: {type(exc).__name__}",
                    plan,
                ),
            ]
        await self._audit(decisions)
        return decisions

    async def _before(self, ctx: Any, req: Any, rsp: FilterResult) -> None:
        if not isinstance(req, ExecutionPlan):
            raise TypeError("review policy requires ExecutionPlan")
        decisions = await self.evaluate_and_audit(req)
        rsp.rsp = decisions
        rsp.is_continue = all(item.action == DecisionAction.ALLOW for item in decisions)

    def _check_integrity(self, plan: ExecutionPlan) -> FilterDecision:
        valid = plan.digest == calculate_plan_digest(plan)
        action = DecisionAction.ALLOW if valid else DecisionAction.DENY
        reason = "plan digest verified" if valid else "plan changed after construction"
        return _decision(action, "plan.integrity", reason, plan)

    def _check_command(self, plan: ExecutionPlan) -> FilterDecision:
        valid = plan.argv == FIXED_SCAN_ARGV and plan.argv[0] in ALLOWED_INTERPRETERS
        action = DecisionAction.ALLOW if valid else DecisionAction.DENY
        reason = "fixed scanner argv" if valid else "command is not the fixed scanner argv"
        return _decision(action, "command.allowlist", reason, plan)

    def _check_paths(self, plan: ExecutionPlan) -> FilterDecision:
        paths = (plan.cwd, plan.input_path, plan.output_path)
        valid = all(_is_safe_relative_path(path) for path in paths)
        action = DecisionAction.ALLOW if valid else DecisionAction.DENY
        reason = "workspace-relative paths" if valid else "unsafe execution path"
        return _decision(action, "path.workspace", reason, plan)

    def _check_environment(self, plan: ExecutionPlan) -> FilterDecision:
        valid = all(name in ALLOWED_ENV_NAMES for name, _ in plan.environment)
        action = DecisionAction.ALLOW if valid else DecisionAction.DENY
        reason = "environment allowlist" if valid else "environment key is not allowed"
        return _decision(action, "environment.allowlist", reason, plan)

    def _check_runtime(self, plan: ExecutionPlan) -> FilterDecision:
        supported = plan.runtime in _SUPPORTED_RUNTIMES
        local_allowed = os.getenv(LOCAL_RUNTIME_OPT_IN_ENV) == "1"
        valid = supported and not plan.network_allowed
        if plan.runtime == "local" and not local_allowed:
            valid = False
        action = DecisionAction.ALLOW if valid else DecisionAction.DENY
        if valid and plan.runtime == "local":
            reason = "unsafe local runtime explicitly enabled for development"
        else:
            reason = "runtime isolation policy" if valid else "runtime or network policy rejected"
        return _decision(action, "runtime.isolation", reason, plan)

    def _check_budget(self, plan: ExecutionPlan) -> FilterDecision:
        valid = (plan.timeout_seconds <= SANDBOX_TIMEOUT_SECONDS and plan.output_limit_bytes <= MAX_OUTPUT_BYTES)
        action = DecisionAction.ALLOW if valid else DecisionAction.DENY
        reason = "execution budget accepted" if valid else "execution budget exceeded"
        return _decision(action, "budget.limit", reason, plan)


async def run_guarded(
    plan: ExecutionPlan,
    policy: ReviewPolicyFilter,
    handler: Callable[[ExecutionPlan], Awaitable[Any]],
) -> tuple[Any | None, list[FilterDecision]]:
    """Run handler only after every audited decision allows it."""
    decisions = await policy.evaluate_and_audit(plan)
    if any(item.action != DecisionAction.ALLOW for item in decisions):
        return None, decisions
    return await handler(plan), decisions
