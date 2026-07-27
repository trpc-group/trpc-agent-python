"""Policy-bounded runtime facade for model-driven code-review tools.

The SDK's one-shot runners return fully materialized stdout and stderr.  Its
tools truncate those strings only *after* the backend has collected them,
which is too late for an infinite or very large producer.  This facade runs
every child behind the bounded capture program used by the deterministic
review path, redacts the bounded result, and deliberately exposes no
``start_program`` method.

The filesystem side also closes the legacy ``collect``/implicit ``out/**``
paths and defensively enforces declarative output limits before a backend can
inline output files.
"""

from __future__ import annotations

import inspect
import sys
from typing import Any

from trpc_agent_sdk.code_executors import (
    META_FILE_NAME,
    BaseProgramRunner,
    BaseWorkspaceRuntime,
    CodeFile,
    ManifestOutput,
    WorkspaceCapabilities,
    WorkspaceInfo,
    WorkspaceOutputSpec,
    WorkspaceRunProgramSpec,
    WorkspaceRunResult,
)

from .filtering import ReviewExecutionFilter
from .redaction import redact_text
from .workspace_sandbox import (
    CAPTURE_PROGRAM_SOURCE,
    CAPTURE_PROTOCOL_PREFIX,
    CAPTURE_SHUTDOWN_GRACE_SECONDS,
    REDACTION_LOOKAHEAD_BYTES,
    OutputCaptureError,
    WorkspaceSandboxRunner,
)

OUTPUT_LIMIT_MARKER = "\n[output limit exceeded; process terminated]"
OUTPUT_TRUNCATION_MARKER = "\n[output truncated]"


def _slice_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    """Slice text to an exact UTF-8 byte ceiling without splitting a codepoint."""
    limit = max(int(max_bytes), 0)
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value, False
    return encoded[:limit].decode("utf-8", errors="ignore"), True


def _redact_and_bound(
    value: str,
    max_bytes: int,
    *,
    truncation_marker: str = OUTPUT_TRUNCATION_MARKER,
) -> tuple[str, bool]:
    """Redact before truncating and keep the marker inside the byte ceiling."""
    redacted, _ = redact_text(value or "")
    limit = max(int(max_bytes), 0)
    encoded = redacted.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return redacted, False

    marker = truncation_marker.encode("utf-8")
    if len(marker) >= limit:
        return marker[:limit].decode("utf-8", errors="ignore"), True
    prefix = encoded[: limit - len(marker)].decode("utf-8", errors="ignore")
    return prefix + truncation_marker, True


def _append_bounded_marker(value: str, marker: str, max_bytes: int) -> str:
    """Append a terminal marker, replacing a suffix when the stream is full."""
    limit = max(int(max_bytes), 0)
    marker_bytes = marker.encode("utf-8")
    if len(marker_bytes) >= limit:
        return marker_bytes[:limit].decode("utf-8", errors="ignore")
    encoded = value.encode("utf-8", errors="replace")
    prefix = encoded[: limit - len(marker_bytes)].decode("utf-8", errors="ignore")
    return prefix + marker


class ReviewBoundedProgramRunner(BaseProgramRunner):
    """One-shot-only runner that bounds and redacts before returning to the SDK."""

    def __init__(
        self,
        delegate: BaseProgramRunner,
        policy: ReviewExecutionFilter,
        *,
        wrapper_python: str,
    ) -> None:
        super().__init__()
        self._delegate = delegate
        self._policy = policy
        self._wrapper_python = wrapper_python

    async def run_program(
        self,
        ws: WorkspaceInfo,
        spec: WorkspaceRunProgramSpec,
        ctx: Any = None,
    ) -> WorkspaceRunResult:
        """Run a command through the bounded capture protocol.

        ``start_program`` is intentionally absent.  ``workspace_exec`` and
        ``skill_exec`` therefore cannot open background or TTY sessions whose
        streaming buffers sit outside this boundary.
        """
        if not spec.cmd:
            raise ValueError("workspace command is empty")
        if spec.tty:
            raise ValueError("interactive TTY execution is disabled for code review")

        requested_timeout = float(spec.timeout or 0)
        if requested_timeout <= 0:
            requested_timeout = float(self._policy.max_timeout_seconds)
        if requested_timeout > float(self._policy.max_timeout_seconds):
            raise ValueError(
                f"workspace timeout {requested_timeout:g}s exceeds review budget "
                f"{self._policy.max_timeout_seconds:g}s"
            )

        final_limit = int(self._policy.max_output_bytes)
        collection_limit = final_limit + REDACTION_LOOKAHEAD_BYTES
        command = [spec.cmd, *(spec.args or [])]
        backend_result = await self._delegate.run_program(
            ws,
            WorkspaceRunProgramSpec(
                cmd=self._wrapper_python,
                args=[
                    "-c",
                    CAPTURE_PROGRAM_SOURCE,
                    str(collection_limit),
                    str(requested_timeout),
                    CAPTURE_PROTOCOL_PREFIX,
                    *command,
                ],
                env=dict(spec.env or {}),
                cwd=spec.cwd,
                # CAPTURE_PROGRAM_SOURCE forwards this stream directly to the
                # child.  The policy filter has already rejected credentials.
                stdin=spec.stdin,
                timeout=requested_timeout + CAPTURE_SHUTDOWN_GRACE_SECONDS,
                limits=spec.limits,
                tty=False,
            ),
            ctx,
        )
        captured = WorkspaceSandboxRunner._decode_captured_result(
            backend_result,
            collection_limit,
        )
        if captured.wrapper_error:
            safe_error, _ = _redact_and_bound(captured.wrapper_error, final_limit)
            raise OutputCaptureError(safe_error or "bounded output wrapper failed")

        stdout, stdout_truncated = _redact_and_bound(captured.stdout, final_limit)
        stderr, stderr_truncated = _redact_and_bound(captured.stderr, final_limit)
        output_limited = (
            captured.output_truncated or stdout_truncated or stderr_truncated
        )
        if output_limited:
            stderr = _append_bounded_marker(
                stderr,
                OUTPUT_LIMIT_MARKER,
                final_limit,
            )

        exit_code = captured.exit_code
        if output_limited and (exit_code is None or exit_code == 0):
            exit_code = 137
        elif captured.timed_out and (exit_code is None or exit_code == 0):
            exit_code = 124

        return WorkspaceRunResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code if exit_code is not None else 1,
            duration=backend_result.duration,
            timed_out=captured.timed_out,
        )


class ReviewBoundedWorkspaceFS:
    """Filesystem proxy that permits only explicit, bounded output manifests."""

    def __init__(self, delegate: Any, policy: ReviewExecutionFilter) -> None:
        self._delegate = delegate
        self._policy = policy

    async def collect(self, ws, patterns, ctx=None) -> list[CodeFile]:
        """Allow the stager's metadata read, but deny legacy/implicit exports."""
        if list(patterns or []) != [META_FILE_NAME]:
            raise ValueError(
                "legacy output collection is disabled; use an explicit bounded outputs manifest"
            )
        manifest = await self.collect_outputs(
            ws,
            WorkspaceOutputSpec(
                globs=[META_FILE_NAME],
                max_files=1,
                max_file_bytes=self._policy.max_output_bytes,
                max_total_bytes=self._policy.max_output_bytes,
                inline=True,
                save=False,
            ),
            ctx,
        )
        return [
            CodeFile(
                name=file_ref.name,
                content=file_ref.content,
                mime_type=file_ref.mime_type,
                size_bytes=len(file_ref.content.encode("utf-8", errors="replace")),
                truncated=manifest.limits_hit,
            )
            for file_ref in manifest.files
        ]

    async def collect_outputs(
        self,
        ws,
        spec: WorkspaceOutputSpec,
        ctx=None,
    ) -> ManifestOutput:
        """Validate before collection, then redact and re-bound inline content."""
        limits = (spec.max_files, spec.max_file_bytes, spec.max_total_bytes)
        if any(isinstance(value, bool) or int(value) <= 0 for value in limits):
            raise ValueError("outputs must set positive explicit collection limits")
        if spec.max_files > self._policy.max_output_files:
            raise ValueError("outputs max_files exceeds the review policy")
        if len(spec.globs) > self._policy.max_output_files:
            raise ValueError("output glob count exceeds the review policy")
        if spec.max_file_bytes > self._policy.max_output_bytes:
            raise ValueError("outputs max_file_bytes exceeds the review policy")
        if spec.max_total_bytes > self._policy.max_output_bytes:
            raise ValueError("outputs max_total_bytes exceeds the review policy")
        if spec.save:
            raise ValueError(
                "saving raw workspace outputs as artifacts is disabled for code review"
            )

        try:
            manifest = await self._delegate.collect_outputs(ws, spec, ctx)
        except Exception as ex:  # noqa: BLE001 - sanitize every backend failure
            safe_error, _ = _redact_and_bound(str(ex), self._policy.max_output_bytes)
            raise OutputCaptureError(
                safe_error or "bounded output collection failed"
            ) from None
        files = list(manifest.files[: spec.max_files])
        limits_hit = bool(manifest.limits_hit or len(manifest.files) > len(files))
        remaining = spec.max_total_bytes
        bounded_files = []
        for file_ref in files:
            redacted, _ = redact_text(file_ref.content or "")
            redacted_name, _ = redact_text(file_ref.name or "")
            safe_name, name_truncated = _slice_utf8(
                redacted_name,
                min(self._policy.max_output_bytes, 4096),
            )
            content_limit = min(spec.max_file_bytes, max(remaining, 0))
            content, truncated = _slice_utf8(redacted, content_limit)
            content_bytes = len(content.encode("utf-8", errors="replace"))
            remaining -= content_bytes
            limits_hit = limits_hit or truncated or name_truncated
            bounded_files.append(
                file_ref.model_copy(
                    update={
                        "name": safe_name,
                        "content": content,
                        # ``save=True`` is rejected above.  Do not propagate
                        # unexpected backend artifact references either.
                        "saved_as": "",
                        "version": 0,
                    },
                    deep=True,
                )
            )
        return ManifestOutput(files=bounded_files, limits_hit=limits_hit)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class ReviewBoundedWorkspaceRuntime(BaseWorkspaceRuntime):
    """Runtime facade shared by every model-reachable code-review tool."""

    def __init__(
        self,
        delegate: BaseWorkspaceRuntime,
        policy: ReviewExecutionFilter,
        *,
        wrapper_python: str | None = None,
    ) -> None:
        if float(policy.max_timeout_seconds) < 1:
            raise ValueError("execution policy timeout must be at least one second")
        if int(policy.max_output_bytes) <= 0:
            raise ValueError("execution policy output budget must be positive")
        if int(policy.max_output_files) <= 0:
            raise ValueError("execution policy output file budget must be positive")
        self._delegate = delegate
        self.policy = policy
        self._wrapper_python = wrapper_python or sys.executable

    def manager(self, ctx=None):
        return self._delegate.manager(ctx)

    def fs(self, ctx=None):
        return ReviewBoundedWorkspaceFS(self._delegate.fs(ctx), self.policy)

    def runner(self, ctx=None):
        return ReviewBoundedProgramRunner(
            self._delegate.runner(ctx),
            self.policy,
            wrapper_python=self._wrapper_python,
        )

    def describe(self, ctx=None) -> WorkspaceCapabilities:
        return self._delegate.describe(ctx)

    async def destroy(self) -> None:
        destroy = getattr(self._delegate, "destroy", None)
        if callable(destroy):
            result = destroy()
            if inspect.isawaitable(result):
                await result
            return
        container_client = getattr(self._delegate, "container", None)
        cleanup = getattr(container_client, "_cleanup_container", None)
        if callable(cleanup):
            result = cleanup()
            if inspect.isawaitable(result):
                await result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


__all__ = [
    "OUTPUT_LIMIT_MARKER",
    "ReviewBoundedProgramRunner",
    "ReviewBoundedWorkspaceFS",
    "ReviewBoundedWorkspaceRuntime",
]
