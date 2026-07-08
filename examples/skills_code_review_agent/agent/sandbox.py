"""Sandbox runner adapters for fake, local, container, and Cube runtimes."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from typing import Any

from .filter_policy import SandboxRequest
from .models import DiffInput
from .models import SandboxRun
from .redaction import redact_text

ENV_WHITELIST = {
    "PATH",
    "PYTHONPATH",
    "LANG",
    "LC_ALL",
    "CR_TEST_COMMAND",
    "CR_ALLOW_TEST_COMMAND",
    "CR_TEST_TIMEOUT",
    "CR_REPO_PATH",
}
REPO_STAGE_MAX_FILES = 300
REPO_STAGE_MAX_FILE_BYTES = 512_000
REPO_STAGE_MAX_TOTAL_BYTES = 8_000_000
REPO_STAGE_FORBIDDEN_MARKERS = (".env", ".ssh/", "id_rsa", "private_key", ".aws/", "/etc/", "secrets/")


class SandboxRunner:
    runtime_name = "base"

    async def run(self, request: SandboxRequest, diff: DiffInput, *, skill_dir: Path) -> SandboxRun:
        raise NotImplementedError


class FakeSandboxRunner(SandboxRunner):
    runtime_name = "fake"

    async def run(self, request: SandboxRequest, diff: DiffInput, *, skill_dir: Path) -> SandboxRun:
        start = time.monotonic()
        await asyncio.sleep(0)
        if "force_sandbox_timeout" in diff.diff_text:
            return SandboxRun(
                name=request.name,
                runtime=self.runtime_name,
                command=request.command,
                status="timeout",
                exit_code=-1,
                duration_ms=max(1, int(request.timeout_sec * 1000)),
                stdout="",
                stderr="simulated sandbox timeout",
                timed_out=True,
                exception_type="TimeoutError",
            )
        if request.name == "forced_failure_probe":
            return SandboxRun(
                name=request.name,
                runtime=self.runtime_name,
                command=request.command,
                status="failed",
                exit_code=2,
                duration_ms=_elapsed_ms(start),
                stdout="",
                stderr="simulated static checker failure",
                exception_type="SimulatedSandboxFailure",
            )
        if "force_large_sandbox_output" in diff.diff_text:
            stdout = "x" * (request.max_output_bytes + 128)
            return SandboxRun(
                name=request.name,
                runtime=self.runtime_name,
                command=request.command,
                status="passed",
                exit_code=0,
                duration_ms=_elapsed_ms(start),
                stdout=stdout[:request.max_output_bytes],
                stderr="",
                output_truncated=True,
            )
        if "force_sandbox_secret_output" in diff.diff_text:
            return _build_run(
                request=request,
                runtime=self.runtime_name,
                start=start,
                stdout="OPENAI_API_KEY=not-a-real-openai-key-abcdefghijklmnopqrstuvwxyz",
                stderr="password=super-secret-password",
                exit_code=0,
                timed_out=False,
            )
        if request.name == "scanner_probe" and "force_scanner_finding" in diff.diff_text:
            return _build_run(
                request=request,
                runtime=self.runtime_name,
                start=start,
                stdout=('{"scanner_runs":[{"name":"bandit","status":"issues_found","findings":['
                        '{"rule_id":"scanner.bandit.B602","severity":"high","file":"app/scanner_target.py",'
                        '"line":4,"title":"subprocess_popen_with_shell_equals_true",'
                        '"evidence":"subprocess.Popen(cmd, shell=True)",'
                        '"recommendation":"Avoid shell=True for subprocess calls.","confidence":0.88}]}]}'),
                stderr="",
                exit_code=0,
                timed_out=False,
            )
        stdout = f"checked_files={len(diff.files)} added_lines={len(diff.added_lines)}"
        return SandboxRun(
            name=request.name,
            runtime=self.runtime_name,
            command=request.command,
            status="passed",
            exit_code=0,
            duration_ms=_elapsed_ms(start),
            stdout=stdout[:request.max_output_bytes],
            stderr="",
            output_truncated=len(stdout) > request.max_output_bytes,
        )


class LocalSandboxRunner(SandboxRunner):
    runtime_name = "local"

    async def run(self, request: SandboxRequest, diff: DiffInput, *, skill_dir: Path) -> SandboxRun:
        cmd = _normalize_python_command(request.command)
        start = time.monotonic()
        process = await asyncio.create_subprocess_shell(
            cmd,
            cwd=str(skill_dir),
            env=_local_env_for_diff(request.env, diff),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                process.communicate(diff.diff_text.encode()),
                timeout=request.timeout_sec,
            )
            timed_out = False
        except asyncio.TimeoutError:
            process.kill()
            with contextlib.suppress(Exception):
                await process.wait()
            stdout_b, stderr_b = b"", b"local sandbox timed out"
            timed_out = True
        return _build_run(
            request=request,
            runtime=self.runtime_name,
            start=start,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
            exit_code=process.returncode,
            timed_out=timed_out,
        )


class WorkspaceSandboxRunner(SandboxRunner):

    def __init__(self, runtime: Any, runtime_name: str):
        self.runtime = runtime
        self.runtime_name = runtime_name

    async def run(self, request: SandboxRequest, diff: DiffInput, *, skill_dir: Path) -> SandboxRun:
        start = time.monotonic()
        manager = None
        workspace_exec_id = f"{request.name}-{uuid.uuid4().hex[:8]}"
        try:
            WorkspacePutFileInfo, WorkspaceRunProgramSpec = _workspace_types()
            manager = self.runtime.manager()
            fs = self.runtime.fs()
            runner = self.runtime.runner()
            ws = await manager.create_workspace(workspace_exec_id)
            files = [
                WorkspacePutFileInfo(path="work/input.diff", content=diff.diff_text.encode()),
                WorkspacePutFileInfo(
                    path=f"work/{Path(request.script_path).name}",
                    content=(skill_dir / request.script_path).read_bytes(),
                ),
            ]
            repo_files = _workspace_repo_files(diff) if _request_allows_repo_read(request) else []
            files.extend(WorkspacePutFileInfo(path=f"repo/{rel}", content=data) for rel, data in repo_files)
            await fs.put_files(ws, files)
            env = _request_env(request.env)
            if repo_files:
                env["CR_REPO_PATH"] = "repo"
            result = await runner.run_program(
                ws,
                WorkspaceRunProgramSpec(
                    cmd="python",
                    args=_workspace_script_args(request),
                    timeout=request.timeout_sec,
                    env=env,
                ),
            )
            return _build_run(
                request=request,
                runtime=self.runtime_name,
                start=start,
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )
        except Exception as exc:  # pylint: disable=broad-except
            redacted = redact_text(str(exc))
            return SandboxRun(
                name=request.name,
                runtime=self.runtime_name,
                command=request.command,
                status="failed",
                exit_code=None,
                duration_ms=_elapsed_ms(start),
                stdout="",
                stderr=redacted.text[:request.max_output_bytes],
                exception_type=exc.__class__.__name__,
                output_truncated=len(redacted.text) > request.max_output_bytes,
            )
        finally:
            if manager is not None:
                with contextlib.suppress(Exception):
                    await manager.cleanup(workspace_exec_id)


def _build_run(
    *,
    request: SandboxRequest,
    runtime: str,
    start: float,
    stdout: str,
    stderr: str,
    exit_code: int | None,
    timed_out: bool,
) -> SandboxRun:
    stdout_r = redact_text(stdout)
    stderr_r = redact_text(stderr)
    stdout_out = stdout_r.text[:request.max_output_bytes]
    stderr_out = stderr_r.text[:request.max_output_bytes]
    truncated = len(stdout_r.text) > request.max_output_bytes or len(stderr_r.text) > request.max_output_bytes
    status = "timeout" if timed_out else ("passed" if exit_code == 0 else "failed")
    return SandboxRun(
        name=request.name,
        runtime=runtime,
        command=request.command,
        status=status,
        exit_code=exit_code,
        duration_ms=_elapsed_ms(start),
        stdout=stdout_out,
        stderr=stderr_out,
        timed_out=timed_out,
        output_truncated=truncated,
        exception_type="TimeoutError" if timed_out else None,
    )


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _local_env(request_env: dict[str, str]) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if key in ENV_WHITELIST}
    python_dir = str(Path(sys.executable).parent)
    env["PATH"] = f"{python_dir}{os.pathsep}{env.get('PATH', '')}" if env.get("PATH") else python_dir
    env.update(_request_env(request_env))
    return env


def _local_env_for_diff(request_env: dict[str, str], diff: DiffInput) -> dict[str, str]:
    env = _local_env(request_env)
    repo_path = _repo_source_path(diff)
    if repo_path is not None:
        env["CR_REPO_PATH"] = str(repo_path)
    return env


def _workspace_repo_files(diff: DiffInput) -> list[tuple[str, bytes]]:
    repo_path = _repo_source_path(diff)
    if repo_path is None:
        return []
    candidates = _git_candidate_files(repo_path)
    files: list[tuple[str, bytes]] = []
    total = 0
    for rel in candidates:
        normalized = rel.replace("\\", "/").lstrip("/")
        if not normalized or normalized.startswith("../") or _is_forbidden_repo_path(normalized):
            continue
        path = repo_path / normalized
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > REPO_STAGE_MAX_FILE_BYTES or total + size > REPO_STAGE_MAX_TOTAL_BYTES:
            continue
        files.append((normalized, path.read_bytes()))
        total += size
        if len(files) >= REPO_STAGE_MAX_FILES:
            break
    return files


def _request_allows_repo_read(request: SandboxRequest) -> bool:
    return any(_workspace_path_is_allowed("repo/", (entry, )) for entry in request.read_allowlist)


def _workspace_path_is_allowed(path: str, allowlist: tuple[str, ...]) -> bool:
    normalized = path.replace("\\", "/").lstrip("/")
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in allowlist)


def _repo_source_path(diff: DiffInput) -> Path | None:
    if diff.source.startswith(("fixture:", "stdin")):
        return None
    path = Path(diff.source)
    if path.is_dir() and (path / ".git").exists():
        return path
    return None


def _git_candidate_files(repo_path: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_path), "ls-files", "-co", "--exclude-standard"],
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _is_forbidden_repo_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith(".git/") or any(marker in lowered for marker in REPO_STAGE_FORBIDDEN_MARKERS)


def _request_env(request_env: dict[str, str]) -> dict[str, str]:
    env = {}
    env.update({
        key: _normalize_python_command(value) if key == "CR_TEST_COMMAND" else value
        for key, value in request_env.items() if key in ENV_WHITELIST
    })
    return env


def _workspace_script_args(request: SandboxRequest) -> list[str]:
    """Translate an audited request command into workspace-local script args."""
    tokens = shlex.split(request.command)
    if len(tokens) < 2:
        raise ValueError(f"Sandbox command must invoke a Python skill script: {request.command}")
    interpreter = Path(tokens[0]).name
    if interpreter not in {"python", "python3"} and tokens[0] != sys.executable:
        raise ValueError(f"Workspace sandbox only supports Python skill scripts, got: {tokens[0]}")
    script_token = tokens[1].replace("\\", "/").lstrip("/")
    expected_script = request.script_path.replace("\\", "/").lstrip("/")
    if script_token != expected_script:
        raise ValueError(f"Sandbox command script {script_token!r} does not match request script {expected_script!r}")
    return [f"work/{Path(request.script_path).name}", "work/input.diff", *tokens[2:]]


def _normalize_python_command(command: str) -> str:
    if command == "python":
        return shlex.quote(sys.executable)
    if command.startswith("python "):
        return f"{shlex.quote(sys.executable)} {command.removeprefix('python ')}"
    return command


def _workspace_types():
    try:
        from trpc_agent_sdk.code_executors import WorkspacePutFileInfo
        from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec

        return WorkspacePutFileInfo, WorkspaceRunProgramSpec
    except ModuleNotFoundError:

        @dataclass(slots=True)
        class WorkspacePutFileInfo:
            path: str
            content: bytes

        @dataclass(slots=True)
        class WorkspaceRunProgramSpec:
            cmd: str
            args: list[str]
            timeout: float
            env: dict[str, str]

        return WorkspacePutFileInfo, WorkspaceRunProgramSpec
