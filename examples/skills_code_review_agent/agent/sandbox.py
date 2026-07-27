"""Workspace-style sandbox execution backends for the code review example.

The container backend runs the same ``skills/``, ``work/`` and ``out/`` layout
inside Docker with network disabled. Dry-run mode uses the same layout in a
temporary local workspace as a development fallback.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .filtering import ReviewExecutionFilter
from .models import FilterDecision, SandboxRequest, SandboxRun
from .redaction import redact_text

SAFE_ENV_KEYS = {
    "PATH",
    "PYTHONPATH",
    "PYTHONIOENCODING",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
}

REDACTION_LOOKAHEAD_BYTES = 8192
CAPTURE_READ_CHUNK_BYTES = 4096
CAPTURE_POLL_SECONDS = 0.01
CAPTURE_SHUTDOWN_GRACE_SECONDS = 1.0


@dataclass(frozen=True)
class _BoundedProcessResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    output_limit_hit: bool


class SandboxRunner:
    """Run code-review skill scripts with filter, timeout and output limits."""

    def __init__(
        self,
        *,
        runtime: str,
        skill_dir: Path,
        execution_filter: ReviewExecutionFilter,
        allow_local_fallback: bool = False,
    ) -> None:
        self.runtime = runtime
        self.skill_dir = skill_dir
        self.execution_filter = execution_filter
        self.allow_local_fallback = allow_local_fallback

    def run(self, request: SandboxRequest) -> SandboxRun:
        """Filter and execute one sandbox request."""
        decision = self.execution_filter.evaluate_request(request)
        if not decision.allowed:
            return SandboxRun(
                name=request.name,
                runtime=self.runtime,
                command=request.display_command,
                status="filtered",
                filter_decision=decision,
                error_type="FilterIntercept",
            )

        if self.runtime == "container":
            return self._run_container(request, decision)
        if self.runtime in {"local", "dry-run-local", "auto"}:
            return self._run_local(
                request,
                decision,
                runtime_name="dry-run-local"
                if self.runtime == "auto"
                else self.runtime,
            )
        return SandboxRun(
            name=request.name,
            runtime=self.runtime,
            command=request.display_command,
            status="failed",
            exit_code=None,
            stderr=f"unsupported sandbox runtime: {self.runtime}",
            error_type="UnsupportedRuntime",
            filter_decision=decision,
        )

    def _run_local(
        self, request: SandboxRequest, decision: FilterDecision, *, runtime_name: str
    ) -> SandboxRun:
        started = time.monotonic()
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with tempfile.TemporaryDirectory(prefix="code_review_sandbox_") as tmp:
            workspace = Path(tmp)
            self._prepare_workspace(workspace, request)
            command = self._resolve_command(request.command)
            cwd = workspace / request.cwd
            env = self._safe_env(request.env)
            try:
                completed = self._run_bounded_process(
                    command,
                    cwd=cwd,
                    env=env,
                    timeout_seconds=request.timeout_seconds,
                    max_output_bytes=request.max_output_bytes,
                )
                duration_ms = int((time.monotonic() - started) * 1000)
                stdout, stdout_truncated = self._truncate(
                    completed.stdout, request.max_output_bytes
                )
                stderr, stderr_truncated = self._truncate(
                    completed.stderr, request.max_output_bytes
                )
                if completed.timed_out:
                    return SandboxRun(
                        name=request.name,
                        runtime=runtime_name,
                        command=request.display_command,
                        status="timed_out",
                        exit_code=None,
                        timed_out=True,
                        duration_ms=duration_ms,
                        stdout=stdout,
                        stderr=stderr,
                        output_truncated=(
                            completed.output_limit_hit
                            or stdout_truncated
                            or stderr_truncated
                        ),
                        error_type="TimeoutExpired",
                        filter_decision=decision,
                        started_at=started_at,
                        finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    )

                artifacts, artifacts_truncated = self._collect_outputs(
                    workspace, request
                )
                if completed.output_limit_hit:
                    status = "failed"
                    error_type = "OutputLimitExceeded"
                else:
                    status = "succeeded" if completed.exit_code == 0 else "failed"
                    error_type = (
                        "" if completed.exit_code == 0 else "SandboxProcessError"
                    )
                return SandboxRun(
                    name=request.name,
                    runtime=runtime_name,
                    command=request.display_command,
                    status=status,
                    exit_code=completed.exit_code,
                    timed_out=False,
                    duration_ms=duration_ms,
                    stdout=stdout,
                    stderr=stderr,
                    output_truncated=(
                        completed.output_limit_hit
                        or stdout_truncated
                        or stderr_truncated
                        or artifacts_truncated
                    ),
                    artifacts=artifacts,
                    error_type=error_type,
                    filter_decision=decision,
                    started_at=started_at,
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
            except Exception as ex:  # noqa: BLE001 - sandbox failures become structured audit rows
                stderr, truncated = self._truncate(str(ex), request.max_output_bytes)
                return SandboxRun(
                    name=request.name,
                    runtime=runtime_name,
                    command=request.display_command,
                    status="failed",
                    duration_ms=int((time.monotonic() - started) * 1000),
                    stderr=stderr,
                    output_truncated=truncated,
                    error_type=type(ex).__name__,
                    filter_decision=decision,
                    started_at=started_at,
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )

    def _run_container(
        self, request: SandboxRequest, decision: FilterDecision
    ) -> SandboxRun:
        if shutil.which("docker") is None:
            if self.allow_local_fallback:
                return self._run_local(request, decision, runtime_name="local-fallback")
            return SandboxRun(
                name=request.name,
                runtime="container",
                command=request.display_command,
                status="failed",
                stderr="docker executable not found",
                error_type="ContainerUnavailable",
                filter_decision=decision,
            )

        started = time.monotonic()
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with tempfile.TemporaryDirectory(prefix="code_review_container_") as tmp:
            workspace = Path(tmp)
            self._prepare_workspace(workspace, request)
            container_command = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "-v",
                f"{workspace.resolve()}:/workspace",
                "-w",
                f"/workspace/{request.cwd}",
                "python:3.11-slim",
                *self._resolve_command(request.command, for_container=True),
            ]
            try:
                completed = subprocess.run(
                    container_command,
                    capture_output=True,
                    text=True,
                    timeout=request.timeout_seconds + 5,
                    check=False,
                )
                stdout, stdout_truncated = self._truncate(
                    completed.stdout, request.max_output_bytes
                )
                stderr, stderr_truncated = self._truncate(
                    completed.stderr, request.max_output_bytes
                )
                artifacts, artifacts_truncated = self._collect_outputs(
                    workspace, request
                )
                status = "succeeded" if completed.returncode == 0 else "failed"
                error_type = "" if completed.returncode == 0 else "SandboxProcessError"
                return SandboxRun(
                    name=request.name,
                    runtime="container",
                    command=request.display_command,
                    status=status,
                    exit_code=completed.returncode,
                    timed_out=False,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    stdout=stdout,
                    stderr=stderr,
                    output_truncated=stdout_truncated
                    or stderr_truncated
                    or artifacts_truncated,
                    artifacts=artifacts,
                    error_type=error_type,
                    filter_decision=decision,
                    started_at=started_at,
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )
            except subprocess.TimeoutExpired as ex:
                stdout, stdout_truncated = self._truncate(
                    ex.stdout or "", request.max_output_bytes
                )
                stderr, stderr_truncated = self._truncate(
                    ex.stderr or "", request.max_output_bytes
                )
                return SandboxRun(
                    name=request.name,
                    runtime="container",
                    command=request.display_command,
                    status="timed_out",
                    timed_out=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    stdout=stdout,
                    stderr=stderr,
                    output_truncated=stdout_truncated or stderr_truncated,
                    error_type="TimeoutExpired",
                    filter_decision=decision,
                    started_at=started_at,
                    finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                )

    def _prepare_workspace(self, workspace: Path, request: SandboxRequest) -> None:
        skill_target = workspace / "skills" / "code-review"
        shutil.copytree(self.skill_dir, skill_target)
        for rel_path, content in request.input_files.items():
            target = workspace / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        (workspace / "out").mkdir(parents=True, exist_ok=True)
        (workspace / "work" / "inputs").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_command(
        command: list[str], *, for_container: bool = False
    ) -> list[str]:
        resolved = []
        for part in command:
            if part == "$PYTHON":
                resolved.append("python" if for_container else sys.executable)
            else:
                resolved.append(part)
        return resolved

    @staticmethod
    def _safe_env(extra: dict[str, str]) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key in SAFE_ENV_KEYS}
        for key, value in extra.items():
            if key in SAFE_ENV_KEYS or key.startswith("TRPC_REVIEW_"):
                env[key] = value
        env.setdefault("PYTHONIOENCODING", "utf-8")
        return env

    @classmethod
    def _run_bounded_process(
        cls,
        command: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> _BoundedProcessResult:
        """Run a local command without materializing unbounded pipe output."""
        final_limit = max(int(max_output_bytes), 0)
        collection_limit = final_limit + REDACTION_LOOKAHEAD_BYTES
        popen_options: dict[str, object] = {}
        if os.name == "posix":
            popen_options["start_new_session"] = True
        elif os.name == "nt":
            popen_options["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )

        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            **popen_options,
        )
        assert process.stdout is not None
        assert process.stderr is not None

        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        limit_hit = threading.Event()
        threads = [
            threading.Thread(
                target=cls._drain_bounded_stream,
                args=(process.stdout, buffers["stdout"], collection_limit, limit_hit),
                daemon=True,
                name="review-sandbox-stdout",
            ),
            threading.Thread(
                target=cls._drain_bounded_stream,
                args=(process.stderr, buffers["stderr"], collection_limit, limit_hit),
                daemon=True,
                name="review-sandbox-stderr",
            ),
        ]
        for thread in threads:
            thread.start()

        deadline = time.monotonic() + max(float(timeout_seconds), 0.0)
        timed_out = False
        while process.poll() is None:
            if limit_hit.is_set():
                cls._terminate_process_tree(process)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                cls._terminate_process_tree(process)
                break
            limit_hit.wait(min(CAPTURE_POLL_SECONDS, remaining))

        try:
            exit_code = process.wait(timeout=CAPTURE_SHUTDOWN_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            cls._terminate_process_tree(process)
            try:
                exit_code = process.wait(timeout=CAPTURE_SHUTDOWN_GRACE_SECONDS)
            except subprocess.TimeoutExpired as ex:
                raise RuntimeError(
                    "sandbox process did not stop after forced termination"
                ) from ex

        for thread in threads:
            thread.join(timeout=CAPTURE_SHUTDOWN_GRACE_SECONDS)
        if any(thread.is_alive() for thread in threads):
            # A descendant may still own an inherited pipe after the direct
            # child exits. Kill the whole process group/tree before closing the
            # local handles so the reader threads cannot outlive this run.
            cls._terminate_process_tree(process)
            process.stdout.close()
            process.stderr.close()
            for thread in threads:
                thread.join(timeout=CAPTURE_SHUTDOWN_GRACE_SECONDS)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("sandbox output readers did not stop")
        process.stdout.close()
        process.stderr.close()

        return _BoundedProcessResult(
            exit_code=exit_code,
            stdout=bytes(buffers["stdout"]).decode("utf-8", errors="replace"),
            stderr=bytes(buffers["stderr"]).decode("utf-8", errors="replace"),
            timed_out=timed_out,
            output_limit_hit=limit_hit.is_set(),
        )

    @staticmethod
    def _drain_bounded_stream(
        stream: object,
        destination: bytearray,
        collection_limit: int,
        limit_hit: threading.Event,
    ) -> None:
        """Drain one binary pipe into a fixed-size buffer."""
        while True:
            remaining = collection_limit - len(destination)
            if remaining <= 0:
                limit_hit.set()
                return
            try:
                chunk = stream.read(min(CAPTURE_READ_CHUNK_BYTES, remaining))
            except (OSError, ValueError):
                return
            if not chunk:
                return
            destination.extend(chunk)
            if len(destination) >= collection_limit:
                limit_hit.set()
                return

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
        """Force-stop a process group on POSIX or a process tree on Windows."""
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        elif os.name == "nt":
            taskkill = shutil.which("taskkill")
            if taskkill is not None:
                try:
                    subprocess.run(
                        [taskkill, "/PID", str(process.pid), "/T", "/F"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=CAPTURE_SHUTDOWN_GRACE_SECONDS,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                except (OSError, subprocess.SubprocessError):
                    pass
        if process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass

    @staticmethod
    def _truncate(value: str, max_bytes: int) -> tuple[str, bool]:
        redacted, _ = redact_text(value or "")
        encoded = redacted.encode("utf-8", errors="replace")
        limit = max(int(max_bytes), 0)
        if len(encoded) <= limit:
            return redacted, False
        # Drop only an incomplete trailing code point. Using ``replace`` here
        # can turn one partial source byte into a three-byte replacement glyph
        # and make the supposedly bounded visible prefix exceed its byte cap.
        truncated = encoded[:limit].decode("utf-8", errors="ignore")
        return truncated + "\n[output truncated]", True

    @classmethod
    def _collect_outputs(
        cls, workspace: Path, request: SandboxRequest
    ) -> tuple[dict[str, str], bool]:
        """Collect declared artifacts with per-file and aggregate hard caps."""
        artifacts: dict[str, str] = {}
        final_limit = max(int(request.max_output_bytes), 0)
        per_file_limit = final_limit + REDACTION_LOOKAHEAD_BYTES
        total_limit = per_file_limit * max(len(request.output_files), 1)
        total_read = 0
        output_truncated = False
        for rel_path in request.output_files:
            target = workspace / rel_path
            if not target.is_file():
                continue
            remaining_total = max(total_limit - total_read, 0)
            read_limit = min(per_file_limit, remaining_total)
            with target.open("rb") as artifact_file:
                raw = artifact_file.read(read_limit + 1)
            if len(raw) > read_limit:
                raw = raw[:read_limit]
                output_truncated = True
            total_read += len(raw)
            content = raw.decode("utf-8", errors="replace")
            visible, visible_truncated = cls._truncate(content, final_limit)
            artifacts[rel_path] = visible
            output_truncated = output_truncated or visible_truncated
        return artifacts, output_truncated
