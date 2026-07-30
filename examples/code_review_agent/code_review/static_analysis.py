"""Deterministic Ruff, Bandit, and Pytest execution with optional Docker isolation."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .models import AnalyzerExecution, AnalyzerStatus, ChangedFile, Finding, Severity


@dataclass(frozen=True)
class StaticAnalysisConfig:
    """Static analyzer selection, isolation, and resource limits."""

    runtime: str = "local"
    enable_ruff: bool = True
    enable_bandit: bool = True
    run_tests: bool = False
    strict_tools: bool = False
    timeout_seconds: float = 120.0
    max_output_chars: int = 100_000
    docker_image: str = "trpc-code-review:latest"
    docker_memory: str = "512m"
    docker_cpus: str = "1.0"
    docker_pids_limit: int = 128

    def __post_init__(self) -> None:
        if self.runtime not in {"local", "docker"}:
            raise ValueError("static analysis runtime must be 'local' or 'docker'")
        if self.timeout_seconds <= 0:
            raise ValueError("static analysis timeout must be positive")
        if self.max_output_chars <= 0:
            raise ValueError("static analysis output limit must be positive")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/:@-]*", self.docker_image):
            raise ValueError("docker image must be a valid image reference")
        if not re.fullmatch(r"[1-9][0-9]*(?:[bkmgBKMG])?", self.docker_memory):
            raise ValueError("docker memory must be a positive integer with an optional b/k/m/g suffix")
        try:
            cpus = float(self.docker_cpus)
        except ValueError as exc:
            raise ValueError("docker cpus must be a positive number") from exc
        if cpus <= 0:
            raise ValueError("docker cpus must be a positive number")
        if self.docker_pids_limit <= 0:
            raise ValueError("docker pids limit must be positive")


@dataclass(frozen=True)
class CommandResult:
    """Raw subprocess outcome independent of analyzer semantics."""

    command: list[str]
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False
    unavailable: bool = False


@dataclass
class StaticAnalysisResult:
    """Aggregate static analyzer findings and execution evidence."""

    findings: list[Finding] = field(default_factory=list)
    executions: list[AnalyzerExecution] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)

    def prompt_text(self) -> str:
        """Return bounded evidence for semantic review and deduplication."""
        payload = {
            "findings": [finding.model_dump(mode="json") for finding in self.findings],
            "executions": [{
                "tool": execution.tool,
                "status": execution.status.value,
                "exit_code": execution.exit_code,
                "stderr": execution.stderr,
            } for execution in self.executions],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


class CommandRunner(Protocol):
    """Execution boundary used by real runtimes and unit tests."""

    runtime_name: str

    def run(self, command: list[str], *, repository: Path, timeout: float) -> CommandResult:
        """Execute an analyzer command."""


class LocalCommandRunner:
    """Execute analyzer binaries directly without a shell."""

    runtime_name = "local"

    def run(self, command: list[str], *, repository: Path, timeout: float) -> CommandResult:
        started = time.monotonic()
        try:
            result = subprocess.run(
                command,
                cwd=repository,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return CommandResult(
                command=command,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_seconds=time.monotonic() - started,
            )
        except FileNotFoundError as exc:
            return CommandResult(
                command=command,
                exit_code=None,
                stderr=str(exc),
                duration_seconds=time.monotonic() - started,
                unavailable=True,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=command,
                exit_code=None,
                stdout=_decode_timeout_stream(exc.stdout),
                stderr=_decode_timeout_stream(exc.stderr),
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )


class DockerCommandRunner(LocalCommandRunner):
    """Run analyzers in an offline, read-only, resource-limited container."""

    runtime_name = "docker"

    def __init__(
        self,
        *,
        image: str,
        memory: str,
        cpus: str,
        pids_limit: int,
    ):
        self.image = image
        self.memory = memory
        self.cpus = cpus
        self.pids_limit = pids_limit

    def build_command(self, command: list[str], repository: Path) -> list[str]:
        """Build a Docker invocation without interpolating a shell command."""
        return [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            self.memory,
            "--cpus",
            self.cpus,
            "--user",
            "65532:65532",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,noexec,size=64m",
            "--mount",
            f"type=bind,src={repository},dst=/workspace,readonly",
            "--workdir",
            "/workspace",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "PYTEST_ADDOPTS=-p no:cacheprovider",
            self.image,
            *command,
        ]

    def run(self, command: list[str], *, repository: Path, timeout: float) -> CommandResult:
        return super().run(self.build_command(command, repository), repository=repository, timeout=timeout)


class StaticAnalyzer:
    """Run enabled analyzers only against changed Python files."""

    def __init__(
        self,
        config: StaticAnalysisConfig | None = None,
        *,
        command_runner: CommandRunner | None = None,
    ):
        self.config = config or StaticAnalysisConfig()
        self.command_runner = command_runner or self._create_runner(self.config)

    async def analyze(self, repository: Path, changed_files: list[ChangedFile]) -> StaticAnalysisResult:
        """Run blocking analyzer processes off the event loop."""
        return await asyncio.to_thread(self.analyze_sync, repository, changed_files)

    def analyze_sync(self, repository: Path, changed_files: list[ChangedFile]) -> StaticAnalysisResult:
        """Run enabled analyzers and normalize their native JSON."""
        repository = repository.resolve()
        python_paths = [
            changed_file.path for changed_file in changed_files
            if changed_file.language == "python" and changed_file.change_type.value != "deleted"
        ]
        result = StaticAnalysisResult()
        if not python_paths:
            result.diagnostics.append("Static analysis skipped: no changed Python files")
            return result

        if self.config.enable_ruff:
            self._run_tool(
                result,
                tool="ruff",
                command=["ruff", "check", "--output-format", "json", "--no-cache", "--", *python_paths],
                repository=repository,
                parser=lambda text: parse_ruff_output(text, repository),
                finding_exit_codes={1},
            )
        if self.config.enable_bandit:
            self._run_tool(
                result,
                tool="bandit",
                command=["bandit", "-f", "json", "-q", "--", *python_paths],
                repository=repository,
                parser=lambda text: parse_bandit_output(text, repository),
                finding_exit_codes={1},
            )
        if self.config.run_tests:
            self._run_tool(
                result,
                tool="pytest",
                command=["pytest", "-q", "--disable-warnings", "--maxfail=20"],
                repository=repository,
                parser=lambda _text: [],
                finding_exit_codes=set(),
            )
        return result

    def _run_tool(
        self,
        aggregate: StaticAnalysisResult,
        *,
        tool: str,
        command: list[str],
        repository: Path,
        parser,
        finding_exit_codes: set[int],
    ) -> None:
        raw = self.command_runner.run(
            command,
            repository=repository,
            timeout=self.config.timeout_seconds,
        )
        stdout = _truncate(raw.stdout, self.config.max_output_chars)
        stderr = _truncate(raw.stderr, self.config.max_output_chars)
        findings: list[Finding] = []
        status = AnalyzerStatus.SUCCESS
        if raw.unavailable or _docker_command_unavailable(raw):
            status = AnalyzerStatus.UNAVAILABLE
        elif raw.timed_out:
            status = AnalyzerStatus.TIMED_OUT
        elif raw.exit_code in {0, *finding_exit_codes}:
            try:
                findings = parser(stdout)
                status = AnalyzerStatus.FINDINGS if findings else AnalyzerStatus.SUCCESS
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                status = AnalyzerStatus.FAILED
                stderr = _truncate(f"{stderr}\nUnable to parse {tool} output: {exc}".strip(),
                                   self.config.max_output_chars)
        else:
            status = AnalyzerStatus.FAILED

        execution = AnalyzerExecution(
            tool=tool,
            runtime=self.command_runner.runtime_name,
            command=raw.command,
            status=status,
            exit_code=raw.exit_code,
            duration_seconds=raw.duration_seconds,
            stdout=stdout,
            stderr=stderr,
            findings_count=len(findings),
        )
        aggregate.executions.append(execution)
        aggregate.findings.extend(findings)
        if status not in {AnalyzerStatus.SUCCESS, AnalyzerStatus.FINDINGS}:
            message = f"{tool} {status.value}"
            if stderr:
                message += f": {stderr.splitlines()[-1]}"
            aggregate.diagnostics.append(message)
            if self.config.strict_tools:
                raise RuntimeError(message)

    @staticmethod
    def _create_runner(config: StaticAnalysisConfig) -> CommandRunner:
        if config.runtime == "local":
            return LocalCommandRunner()
        return DockerCommandRunner(
            image=config.docker_image,
            memory=config.docker_memory,
            cpus=config.docker_cpus,
            pids_limit=config.docker_pids_limit,
        )


def parse_ruff_output(text: str, repository: Path) -> list[Finding]:
    """Map Ruff JSON diagnostics to the common Finding schema."""
    if not text.strip():
        return []
    diagnostics = json.loads(text)
    findings: list[Finding] = []
    for diagnostic in diagnostics:
        code = str(diagnostic["code"])
        location = diagnostic["location"]
        end_location = diagnostic.get("end_location") or location
        category, severity = _ruff_classification(code)
        fix = diagnostic.get("fix") or {}
        findings.append(
            Finding(
                rule_id=f"ruff.{code.casefold()}",
                severity=severity,
                confidence=0.99,
                category=category,
                file_path=_relative_path(str(diagnostic["filename"]), repository),
                start_line=int(location["row"]),
                end_line=int(end_location["row"]),
                title=str(diagnostic["message"]),
                description=f"Ruff reported {code}: {diagnostic['message']}",
                suggestion=str(fix.get("message", "")),
                source="static:ruff",
            ))
    return findings


def parse_bandit_output(text: str, repository: Path) -> list[Finding]:
    """Map Bandit JSON diagnostics to the common Finding schema."""
    if not text.strip():
        return []
    payload = json.loads(text)
    findings: list[Finding] = []
    for diagnostic in payload.get("results", []):
        severity = _bandit_severity(str(diagnostic.get("issue_severity", "")))
        confidence = _bandit_confidence(str(diagnostic.get("issue_confidence", "")))
        line_range = diagnostic.get("line_range") or [diagnostic["line_number"]]
        test_id = str(diagnostic["test_id"])
        findings.append(
            Finding(
                rule_id=f"bandit.{test_id.casefold()}",
                severity=severity,
                confidence=confidence,
                category="security",
                file_path=_relative_path(str(diagnostic["filename"]), repository),
                start_line=min(int(line) for line in line_range),
                end_line=max(int(line) for line in line_range),
                title=str(diagnostic["issue_text"]),
                description=f"Bandit reported {test_id}: {diagnostic['issue_text']}",
                suggestion=str(diagnostic.get("more_info", "")),
                source="static:bandit",
            ))
    return findings


def _ruff_classification(code: str) -> tuple[str, Severity]:
    if code.startswith("S"):
        return "security", Severity.HIGH
    if code.startswith(("F", "E9", "B")):
        return "correctness", Severity.MEDIUM
    if code.startswith(("PERF", "PL", "C4", "SIM")):
        return "maintainability", Severity.LOW
    return "quality", Severity.LOW


def _bandit_severity(value: str) -> Severity:
    return {
        "HIGH": Severity.HIGH,
        "MEDIUM": Severity.MEDIUM,
        "LOW": Severity.LOW,
    }.get(value.upper(), Severity.LOW)


def _bandit_confidence(value: str) -> float:
    return {
        "HIGH": 0.95,
        "MEDIUM": 0.8,
        "LOW": 0.6,
    }.get(value.upper(), 0.6)


def _relative_path(path: str, repository: Path) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(repository.resolve()).as_posix()
        except ValueError:
            return candidate.as_posix()
    normalized = candidate.as_posix()
    return normalized.removeprefix("./")


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = "\n[OUTPUT TRUNCATED]\n"
    return value[:max(0, limit - len(marker))] + marker


def _decode_timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _docker_command_unavailable(result: CommandResult) -> bool:
    if not result.command or result.command[0] != "docker":
        return False
    message = f"{result.stdout}\n{result.stderr}".casefold()
    return result.exit_code in {125, 127} and ("no such image" in message or "executable file not found" in message
                                               or "cannot connect" in message or "permission denied" in message)
