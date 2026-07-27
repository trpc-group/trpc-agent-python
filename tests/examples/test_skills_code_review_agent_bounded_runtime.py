"""Tests for the model-driven review runtime's hard output boundary."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import pytest

from examples.skills_code_review_agent.agent.bounded_runtime import (
    OUTPUT_LIMIT_MARKER,
    ReviewBoundedWorkspaceFS,
    ReviewBoundedWorkspaceRuntime,
)
from examples.skills_code_review_agent.agent.filtering import ReviewExecutionFilter
from trpc_agent_sdk.code_executors import (
    ManifestFileRef,
    ManifestOutput,
    WorkspaceCapabilities,
    WorkspaceInfo,
    WorkspaceOutputSpec,
    WorkspaceRunProgramSpec,
    WorkspaceRunResult,
)


class SubprocessBackendRunner:
    """Materializing backend used to prove it only receives a bounded envelope."""

    def __init__(self) -> None:
        self.calls = []
        self.backend_stdout = ""

    async def run_program(self, ws, spec, ctx=None) -> WorkspaceRunResult:
        self.calls.append((ws, spec))
        started = time.monotonic()
        completed = await asyncio.to_thread(
            subprocess.run,
            [spec.cmd, *spec.args],
            input=spec.stdin,
            text=True,
            capture_output=True,
            check=False,
            timeout=spec.timeout + 2,
            env={**os.environ, **spec.env},
        )
        self.backend_stdout = completed.stdout
        return WorkspaceRunResult(
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            duration=time.monotonic() - started,
            timed_out=False,
        )


class DelegateRuntime:
    def __init__(self, *, runner=None, fs=None) -> None:
        self.runner_impl = runner or SubprocessBackendRunner()
        self.fs_impl = fs or object()
        self.destroy_calls = 0

    def manager(self, ctx=None):
        return object()

    def fs(self, ctx=None):
        return self.fs_impl

    def runner(self, ctx=None):
        return self.runner_impl

    def describe(self, ctx=None) -> WorkspaceCapabilities:
        return WorkspaceCapabilities(isolation="test", network_allowed=False)

    async def destroy(self) -> None:
        self.destroy_calls += 1


def _runtime(*, max_output_bytes: int = 128, max_timeout_seconds: int = 3):
    delegate = DelegateRuntime()
    policy = ReviewExecutionFilter(
        max_timeout_seconds=max_timeout_seconds,
        max_output_bytes=max_output_bytes,
        max_output_files=2,
    )
    runtime = ReviewBoundedWorkspaceRuntime(
        delegate,
        policy,
        wrapper_python=sys.executable,
    )
    return runtime, delegate


def test_infinite_stdout_is_killed_before_backend_materialization_grows_unbounded():
    runtime, delegate = _runtime(
        max_output_bytes=96,
        max_timeout_seconds=30,
    )
    producer = "import os\nwhile True:\n    os.write(1, b'x' * 4096)\n"

    result = asyncio.run(
        runtime.runner().run_program(
            WorkspaceInfo(id="bounded", path="."),
            WorkspaceRunProgramSpec(
                cmd=sys.executable,
                args=["-c", producer],
                timeout=30,
            ),
        )
    )

    assert result.exit_code != 0
    assert OUTPUT_LIMIT_MARKER.strip() in result.stderr
    assert len(result.stdout.encode("utf-8")) <= 96
    assert len(result.stderr.encode("utf-8")) <= 96
    # The backend materializes only the base64 protocol envelope (the visible
    # budget plus fixed redaction lookahead), never the producer's 1 MiB stream.
    assert len(delegate.runner_impl.backend_stdout.encode("utf-8")) < 32_000


def test_one_shot_stdin_is_forwarded_and_bounded_streams_are_redacted():
    runtime, delegate = _runtime(max_output_bytes=512)
    secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
    program = (
        "import sys; "
        "print('stdin=' + sys.stdin.read().strip()); "
        f"print('token={secret}', file=sys.stderr)"
    )

    result = asyncio.run(
        runtime.runner().run_program(
            WorkspaceInfo(id="stdin", path="."),
            WorkspaceRunProgramSpec(
                cmd=sys.executable,
                args=["-c", program],
                stdin="ordinary review input\n",
                timeout=3,
            ),
        )
    )

    wrapper_spec = delegate.runner_impl.calls[0][1]
    assert wrapper_spec.stdin == "ordinary review input\n"
    assert "stdin=ordinary review input" in result.stdout
    assert secret not in result.stdout + result.stderr
    assert "<REDACTED>" in result.stderr
    assert result.exit_code == 0
    assert not hasattr(runtime.runner(), "start_program")


class OversizedManifestFS:
    def __init__(self) -> None:
        self.calls = []

    async def collect_outputs(self, ws, spec, ctx=None) -> ManifestOutput:
        self.calls.append(spec)
        secret = "ghp_abcdefghijklmnopqrstuvwxyz123456"
        return ManifestOutput(
            files=[
                ManifestFileRef(
                    name=f"out/{secret}.json",
                    mime_type="application/json",
                    content=f'{{"token":"{secret}","padding":"' + "x" * 200 + '"}',
                    saved_as=f"raw/{secret}.json",
                    version=7,
                )
            ]
        )


def test_output_manifests_are_rebounded_redacted_and_legacy_collection_is_closed():
    delegate = OversizedManifestFS()
    policy = ReviewExecutionFilter(max_output_bytes=80, max_output_files=1)
    fs = ReviewBoundedWorkspaceFS(delegate, policy)
    ws = WorkspaceInfo(id="outputs", path=".")
    spec = WorkspaceOutputSpec(
        globs=["out/result.json"],
        max_files=1,
        max_file_bytes=80,
        max_total_bytes=80,
        inline=True,
        save=False,
    )

    manifest = asyncio.run(fs.collect_outputs(ws, spec))

    content = manifest.files[0].content
    assert "ghp_" not in content
    assert "<REDACTED>" in content
    assert "ghp_" not in manifest.files[0].name
    assert "<REDACTED>" in manifest.files[0].name
    assert manifest.files[0].saved_as == ""
    assert manifest.files[0].version == 0
    assert len(content.encode("utf-8")) <= 80
    assert manifest.limits_hit is True

    with pytest.raises(ValueError, match="legacy output collection"):
        asyncio.run(fs.collect(ws, ["out/**"]))
    with pytest.raises(ValueError, match="saving raw workspace outputs"):
        asyncio.run(
            fs.collect_outputs(
                ws,
                spec.model_copy(update={"save": True}),
            )
        )
    assert len(delegate.calls) == 1


def test_toolset_factory_cleans_only_an_owned_runtime_on_construction_failure(
    monkeypatch,
):
    from examples.skills_code_review_agent.agent import tools as review_tools
    from trpc_agent_sdk import skills as skills_module

    owned = DelegateRuntime()
    external = DelegateRuntime()

    def fail_repository(*args, **kwargs):
        raise RuntimeError("repository construction failed")

    monkeypatch.setattr(review_tools, "create_workspace_runtime", lambda runtime: owned)
    monkeypatch.setattr(
        skills_module,
        "create_default_skill_repository",
        fail_repository,
    )

    async def fail_inside_running_loop() -> None:
        with pytest.raises(RuntimeError, match="repository construction failed"):
            review_tools.create_review_skill_tool_set("local")

    asyncio.run(fail_inside_running_loop())
    with pytest.raises(RuntimeError, match="repository construction failed"):
        review_tools.create_review_skill_tool_set(
            "local",
            workspace_runtime=external,
        )

    assert owned.destroy_calls == 1
    assert external.destroy_calls == 0
