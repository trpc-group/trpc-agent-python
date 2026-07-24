"""Filter-governed fake, container, and local sandbox runners."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import yaml

from models import SandboxResult
from redaction import redact_text


@dataclass(frozen=True)
class FilterDecision:
    decision: str
    reason: str | None = None


class ReviewExecutionFilter:
    def __init__(self, config: dict):
        self.config = config

    def decide(self, command: list[str], timeout: float, call_index: int) -> FilterDecision:
        joined = " ".join(command)
        if call_index > self.config["max_tool_calls"]:
            return FilterDecision("deny", "tool call budget exceeded")
        if timeout <= 0:
            return FilterDecision("deny", "timeout must be positive")
        if any(path in joined for path in self.config["forbidden_paths"]):
            return FilterDecision("deny", "command references a forbidden path")
        if command and command[0] not in self.config["allowed_commands"]:
            return FilterDecision("needs_human_review", "command is not on the allowlist")
        for token in command:
            if token.startswith(("http://", "https://")):
                host = (urlparse(token).hostname or "").lower()
                if host not in self.config["allowed_domains"]:
                    return FilterDecision("deny", "network destination is not allowlisted")
        if any(token in joined for token in ("rm -rf", "sudo ", "| sh", ";")):
            return FilterDecision("deny", "high-risk shell pattern")
        return FilterDecision("allow")


class SandboxRunner:
    """Run approved checks with timeout, output cap, and environment allowlist."""

    def __init__(self, policy_path: str | Path, workspace: str | Path, dry_run: bool = False):
        self.policy = yaml.safe_load(Path(policy_path).read_text(encoding="utf-8"))
        self.sandbox = self.policy["sandbox"]
        self.filter = ReviewExecutionFilter(self.policy["filter"])
        self.workspace = Path(workspace).resolve()
        self.mode = "fake" if dry_run else self.sandbox["mode"]

    def run(self, command: list[str], call_index: int) -> SandboxResult:
        timeout = float(self.sandbox["timeout_seconds"])
        decision = self.filter.decide(command, timeout, call_index)
        safe_command = [redact_text(token)[0] for token in command]
        if decision.decision != "allow":
            return SandboxResult(
                command=safe_command,
                status="blocked",
                exit_code=None,
                duration_ms=0.0,
                output="",
                filter_decision=decision.decision,
                filter_reason=decision.reason,
            )
        started = time.perf_counter()
        try:
            if self.mode == "fake":
                if command and command[0] == "fake-fail":
                    raise subprocess.CalledProcessError(2, command, output="fixture failure")
                output, exit_code = "fake sandbox check passed", 0
            else:
                process_command = self._container_command(command) if self.mode == "container" else command
                completed = subprocess.run(
                    process_command,
                    cwd=None if self.mode == "container" else self.workspace,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                    env=self._allowed_environment(),
                )
                output = completed.stdout + completed.stderr
                exit_code = completed.returncode
            output, redacted = redact_text(output[: self.sandbox["max_output_bytes"]])
            truncated = len(output.encode("utf-8")) >= self.sandbox["max_output_bytes"]
            return SandboxResult(
                command=safe_command,
                status="passed" if exit_code == 0 else "failed",
                exit_code=exit_code,
                duration_ms=(time.perf_counter() - started) * 1000,
                output=output,
                error_type="output_truncated" if truncated else None,
                redacted=redacted,
            )
        except subprocess.TimeoutExpired:
            return self._failure(safe_command, started, "timeout", "sandbox execution timed out")
        except subprocess.CalledProcessError as exc:
            output, redacted = redact_text(str(exc.output or exc))
            result = self._failure(safe_command, started, "process_error", output)
            result.redacted = redacted
            return result
        except Exception as exc:  # sandbox infrastructure failure must not abort review
            output, redacted = redact_text(str(exc))
            result = self._failure(safe_command, started, type(exc).__name__, output)
            result.redacted = redacted
            return result

    def _container_command(self, command: list[str]) -> list[str]:
        return [
            "docker", "run", "--rm", "--network", "none", "--read-only",
            "--memory", str(self.sandbox["memory"]), "--cpus", str(self.sandbox["cpus"]),
            "--pids-limit", "128", "-v", f"{self.workspace}:/workspace:ro", "-w", "/workspace",
            str(self.sandbox["image"]), *command,
        ]

    def _allowed_environment(self) -> dict[str, str]:
        return {
            key: os.environ[key]
            for key in self.sandbox["environment_allowlist"]
            if key in os.environ
        }

    @staticmethod
    def _failure(command: list[str], started: float, error_type: str, output: str) -> SandboxResult:
        return SandboxResult(
            command=command,
            status="failed",
            exit_code=None,
            duration_ms=(time.perf_counter() - started) * 1000,
            output=output,
            error_type=error_type,
        )
