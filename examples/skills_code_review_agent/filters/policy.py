"""Deterministic pre-execution policy for sandbox commands."""

import os
import re
import shlex
import uuid
import math
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path

from pydantic import BaseModel
from pydantic import Field

from reports.models import FilterDecision
from security import is_likely_secret_path


@dataclass(frozen=True)
class ReviewPolicyContext:
    """Trusted input metadata used to narrow commands for one review mode."""

    input_kind: str
    source: str
    scope: str


class SandboxCommand(BaseModel):
    """Requested sandbox operation and its resource budget."""

    command: str = Field(max_length=4096)
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_output_bytes: int = Field(default=64 * 1024, gt=0)
    environment: dict[str, str] = Field(default_factory=dict)
    network_required: bool = False


class CommandPolicy:
    """Block dangerous, networked, secret-bearing, or over-budget commands."""

    # Only deterministic review scripts and bounded read-only tools are auto-approved.
    allowed_commands = frozenset({"git", "python3", "pytest"})
    human_review_commands = frozenset({"bash", "sh", "docker", "sudo", "rm"})
    forbidden_paths = ("/etc", "/root", "/proc", "/sys", "/var/run/docker.sock")
    allowed_environment = frozenset({"LANG", "LC_ALL"})
    locale_value = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
    allowed_python_scripts = frozenset(
        {
            "scripts/inspect_file_list.py",
            "scripts/inspect_files.py",
            "scripts/inspect_git_files.py",
            "scripts/review_async.py",
            "scripts/review_database.py",
            "scripts/review_git_changes.py",
            "scripts/review_resources.py",
            "scripts/review_secrets.py",
            "scripts/review_security.py",
            "scripts/review_tests.py",
            "scripts/run_review_rules.py",
        }
    )
    allowed_python_modules = frozenset({"compileall", "unittest"})
    read_only_git_commands = frozenset({"diff", "status", "ls-files"})
    forbidden_git_options = frozenset(
        {
            "--ext-diff",
            "--textconv",
            "--no-index",
            "--config-env",
            "--exec-path",
        }
    )
    shell_operators = (";", "&", "|", ">", "<", "`", "$", "\n", "\r")
    hard_max_timeout_seconds = 120.0
    hard_max_output_bytes = 1024 * 1024

    def __init__(
        self,
        max_timeout_seconds: float = 120.0,
        max_output_bytes: int = 1024 * 1024,
        context: ReviewPolicyContext | None = None,
        allow_repository_execution: bool = False,
    ) -> None:
        if (
            not math.isfinite(max_timeout_seconds)
            or not 0 < max_timeout_seconds <= self.hard_max_timeout_seconds
        ):
            raise ValueError("max timeout must be between 0 and 120 seconds")
        if not 0 < max_output_bytes <= self.hard_max_output_bytes:
            raise ValueError("max output must be between 1 byte and 1 MiB")
        self.max_timeout_seconds = max_timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.context = context
        self.allow_repository_execution = allow_repository_execution

    @classmethod
    def from_env(
        cls,
        context: ReviewPolicyContext | None = None,
    ) -> "CommandPolicy":
        """Load resource ceilings without changing the fixed safety allowlists."""
        repository_execution = os.getenv(
            "CODE_REVIEW_ALLOW_REPOSITORY_EXECUTION",
            "false",
        ).strip().lower()
        if repository_execution not in {"0", "1", "false", "true", "no", "yes"}:
            raise ValueError(
                "CODE_REVIEW_ALLOW_REPOSITORY_EXECUTION must be true or false"
            )
        return cls(
            max_timeout_seconds=float(
                os.getenv("CODE_REVIEW_MAX_TIMEOUT_SECONDS", "120")
            ),
            max_output_bytes=int(
                os.getenv("CODE_REVIEW_MAX_OUTPUT_BYTES", str(1024 * 1024))
            ),
            context=context,
            allow_repository_execution=repository_execution in {"1", "true", "yes"},
        )

    @staticmethod
    def _deny_reason(reason: str) -> tuple[str, str]:
        return "deny", reason

    @classmethod
    def _validate_pagination(
        cls,
        options: list[str],
        *,
        max_limit: int,
    ) -> tuple[str, str] | None:
        """Validate optional cursor/limit pairs used by bounded JSON readers."""
        seen: set[str] = set()
        while options:
            if len(options) < 2 or options[0] not in {"--cursor", "--limit"}:
                return cls._deny_reason("unsupported pagination option")
            option, raw_value = options[:2]
            if option in seen:
                return cls._deny_reason("pagination options must not be repeated")
            seen.add(option)
            try:
                value = int(raw_value)
            except ValueError:
                return cls._deny_reason("pagination values must be numeric")
            if value < 0 or (option == "--limit" and not 1 <= value <= max_limit):
                return cls._deny_reason("pagination exceeds its configured bound")
            options = options[2:]
        return None

    def _evaluate_review_context(self, tokens: list[str]) -> tuple[str, str] | None:
        """Apply input-mode rules after the generic command checks pass."""
        if self.context is None:
            return None
        kind = self.context.input_kind
        if self.context.scope not in {"changed", "full"}:
            return self._deny_reason("unsupported review scope")
        if kind in {"diff_file", "fixture"}:
            filename = Path(self.context.source).name
            if kind == "fixture":
                filename = f"{filename}.diff"
            expected = [
                "python3",
                "scripts/run_review_rules.py",
                f"work/inputs/{filename}",
            ]
            if tokens[:3] != expected:
                return self._deny_reason(
                    "diff inputs may only use the aggregate paginated rule runner"
                )
            return self._validate_pagination(tokens[3:], max_limit=24)

        if kind == "file_list":
            list_path = f"work/inputs/{self.context.source}"
            list_prefix = ["python3", "scripts/inspect_file_list.py", list_path]
            read_prefix = [
                "python3",
                "scripts/inspect_files.py",
                "work/inputs",
                list_path,
            ]
            if tokens[: len(list_prefix)] == list_prefix:
                return self._validate_pagination(
                    tokens[len(list_prefix) :],
                    max_limit=12,
                )
            if tokens[: len(read_prefix)] == read_prefix:
                return self._validate_pagination(
                    tokens[len(read_prefix) :],
                    max_limit=3,
                )
            return self._deny_reason(
                "file-list inputs may only validate and read the declared list"
            )

        if kind == "git_worktree":
            if tokens[:3] == [
                "python3",
                "scripts/inspect_git_files.py",
                "work/inputs",
            ]:
                expected_mode = (
                    "tracked" if self.context.scope == "full" else "changed"
                )
                options = tokens[3:]
                if len(options) < 2 or options[:2] != ["--mode", expected_mode]:
                    return self._deny_reason(
                        "Git file enumeration does not match the review scope"
                    )
                return self._validate_pagination(options[2:], max_limit=12)
            elif tokens[:3] == [
                "python3",
                "scripts/review_git_changes.py",
                "work/inputs",
            ]:
                if self.context.scope != "changed":
                    return self._deny_reason(
                        "Git diff collection is only valid for changed scope"
                    )
                options = tokens[3:]
                if len(options) < 2 or options[:2] not in (
                    ["--mode", "unstaged"],
                    ["--mode", "staged"],
                ):
                    return self._deny_reason("Git diff mode must be staged or unstaged")
                return self._validate_pagination(options[2:], max_limit=24)
            elif tokens[:3] == ["python3", "scripts/inspect_files.py", "work/inputs"]:
                options = tokens[3:]
                paths: list[str] = []
                pagination: list[str] = []
                scopes: list[str] = []
                while options:
                    if len(options) < 2:
                        return self._deny_reason("repository inspection option is incomplete")
                    option, value = options[:2]
                    if option == "--path":
                        paths.append(value)
                    elif option == "--scope":
                        scopes.append(value)
                    elif option in {"--cursor", "--limit"}:
                        pagination.extend((option, value))
                    else:
                        return self._deny_reason("unsupported repository inspection option")
                    options = options[2:]
                if not paths:
                    return self._deny_reason("repository inspection requires --path")
                if scopes != [self.context.scope]:
                    return self._deny_reason(
                        "repository inspection scope does not match the review"
                    )
                if len(paths) > 12:
                    return self._deny_reason("repository inspection path batch is too large")
                if any(is_likely_secret_path(path) for path in paths):
                    return self._deny_reason("likely secret files require human review")
                invalid_pagination = self._validate_pagination(
                    pagination,
                    max_limit=3,
                )
                if invalid_pagination is not None:
                    return invalid_pagination
            elif tokens[0] == "python3" and tokens[1:3] == ["-m", "compileall"]:
                pass
            elif tokens[0] == "python3" and tokens[1:3] == ["-m", "unittest"]:
                if not self.allow_repository_execution:
                    return (
                        "needs_human_review",
                        "repository code execution is disabled by default",
                    )
            elif tokens[0] == "pytest":
                if not self.allow_repository_execution:
                    return (
                        "needs_human_review",
                        "repository code execution is disabled by default",
                    )
            else:
                return self._deny_reason("command is not valid for repository review")
            return None
        return self._deny_reason(f"unsupported review input kind: {kind}")

    def evaluate(self, request: SandboxCommand) -> FilterDecision:
        """Return a decision before any sandbox operation is attempted."""
        decision = "allow"
        reason = "command is read-only and within the configured budget"
        try:
            tokens = shlex.split(request.command)
        except ValueError as error:
            tokens = []
            decision = "deny"
            reason = f"invalid command syntax: {error}"

        executable = tokens[0] if tokens else ""
        # This ordered chain is fail-closed: the first unsafe condition wins.
        if request.network_required:
            decision, reason = "deny", "network access is not allowed"
        elif request.timeout_seconds > self.max_timeout_seconds:
            decision, reason = "deny", "execution timeout exceeds policy budget"
        elif request.max_output_bytes > self.max_output_bytes:
            decision, reason = "deny", "output limit exceeds policy budget"
        elif any(key not in self.allowed_environment for key in request.environment):
            decision, reason = "deny", "environment contains a non-whitelisted key"
        elif any(
            not self.locale_value.fullmatch(value)
            for value in request.environment.values()
        ):
            decision, reason = "deny", "environment contains an unsafe locale value"
        elif any(path in request.command for path in self.forbidden_paths):
            decision, reason = "deny", "command references a forbidden path"
        elif any(Path(token).is_absolute() for token in tokens[1:] if not token.startswith("-")):
            decision, reason = "deny", "absolute command arguments are not allowed"
        elif any(".." in Path(token).parts for token in tokens[1:]):
            decision, reason = "deny", "path traversal is not allowed"
        elif any(token.startswith("~") for token in tokens[1:]):
            decision, reason = "deny", "home-directory expansion is not allowed"
        elif any(operator in request.command for operator in self.shell_operators):
            decision, reason = (
                "needs_human_review",
                "shell composition requires explicit human review",
            )
        elif executable in self.human_review_commands:
            decision, reason = "needs_human_review", "high-risk executable requires approval"
        elif executable not in self.allowed_commands:
            decision, reason = "deny", f"executable is not allowlisted: {executable or '<empty>'}"
        elif executable == "python3":
            target = tokens[1] if len(tokens) > 1 else ""
            if target == "-m":
                module = tokens[2] if len(tokens) > 2 else ""
                approved = module in self.allowed_python_modules
                target_description = f"Python module is not allowlisted: {module or '<empty>'}"
            else:
                approved = target in self.allowed_python_scripts
                target_description = f"Python script is not allowlisted: {target or '<empty>'}"
            if not approved:
                decision, reason = (
                    "needs_human_review",
                    target_description,
                )
            elif target == "-m" and module == "unittest" and not self.allow_repository_execution:
                decision, reason = (
                    "needs_human_review",
                    "repository code execution is disabled by default",
                )
        elif executable == "pytest" and not self.allow_repository_execution:
            decision, reason = (
                "needs_human_review",
                "repository code execution is disabled by default",
            )
        elif executable == "git":
            git_args = list(tokens[1:])
            while git_args and git_args[0].startswith("-"):
                option = git_args.pop(0)
                if option in {"-C", "--git-dir", "--work-tree"} and git_args:
                    git_args.pop(0)
            subcommand = git_args[0] if git_args else ""
            forbidden_option = next(
                (
                    token
                    for token in tokens[1:]
                    if token in self.forbidden_git_options
                    or token.startswith("--config-env=")
                    or token.startswith("--exec-path=")
                    or token == "-c"
                ),
                None,
            )
            if forbidden_option:
                decision, reason = "deny", f"Git option is not allowed: {forbidden_option}"
            elif subcommand not in self.read_only_git_commands:
                decision, reason = (
                    "needs_human_review",
                    f"Git subcommand is not read-only allowlisted: {subcommand or '<empty>'}",
                )

        if decision == "allow":
            contextual = self._evaluate_review_context(tokens)
            if contextual is not None:
                decision, reason = contextual

        return FilterDecision(
            decision_id=str(uuid.uuid4()),
            command=request.command,
            decision=decision,
            reason=reason,
            created_at=datetime.now(timezone.utc),
        )
