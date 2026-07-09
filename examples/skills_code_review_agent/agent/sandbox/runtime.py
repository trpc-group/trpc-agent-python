# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox runtime — isolated script execution (Phase 3, L4).

Adapter over the SDK's execution backends: the code-review scripts
(``run_checks.py`` etc.) are staged into an isolated workspace and executed,
returning raw stdout/stderr/exit_code. We map that to the CR Agent's
:class:`RunResult` and apply the five safety boundaries from
:class:`SandboxPolicy` (timeout, output cap, env whitelist, secret masking,
fail-safe).

Runtime selection (G1 fix)
--------------------------
The skill's ``sandbox_config`` declares ``default_runtime`` / ``fallback``
(e.g. ``default_runtime: container``, ``fallback: local``). The agent no
longer hardcodes ``LocalRuntime`` — it honors that contract via
:func:`build_runtime_with_fallback`, which probes ``default_runtime`` first
and transparently degrades to ``fallback`` when the requested backend cannot
be provisioned (e.g. the docker daemon is absent). The *actual* runtime used
is returned so the pipeline can record it truthfully in ``sandbox_run``.

Backends
--------
* ``LocalRuntime`` — SDK ``create_local_workspace_runtime`` (process isolation,
  dev / fallback). No external dependency.
* ``ContainerRuntime`` — SDK ``ContainerClient`` (real docker isolation).
  Raises :class:`RuntimeUnavailable` when docker is not reachable so the agent
  can fall back to ``LocalRuntime``.
* ``CubeRuntime`` — SDK ``CubeSandboxClient`` (remote Cube/E2B sandbox). Raises
  :class:`RuntimeUnavailable` when the cube extra / credentials are missing.

Shared notes
------------
* The whole script directory is staged (not just one file) so the script can
  ``import`` sibling modules (``run_checks.py`` imports ``parse_diff``).
* Secrets are masked **before** truncation so masking never misses bytes.
* Any exception is caught → ``status="failed"`` with empty stdout; the
  pipeline never crashes on a sandbox failure.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from typing import runtime_checkable

# Wire the skill scripts dir onto sys.path so we can import mask_secrets
# (shared with the sensitive_info rule set) for output redaction.
_HERE = Path(__file__).resolve().parent  # .../agent/sandbox
_SCRIPTS_DIR = _HERE.parent.parent / "skills" / "code-review" / "scripts"
if str(_SCRIPTS_DIR) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(_SCRIPTS_DIR))
from mask_secrets import mask_secrets  # noqa: E402

from .policy import SandboxPolicy  # noqa: E402

logger = logging.getLogger(__name__)


class RuntimeUnavailable(RuntimeError):
    """The requested sandbox runtime cannot be provisioned in this environment.

    Raised by a runtime's ``ensure_available()`` probe (e.g. docker daemon
    absent, cube not configured). The agent catches it and falls back to the
    configured ``fallback`` runtime.
    """


@dataclass
class RunResult:
    """One sandbox execution outcome, persisted to ``sandbox_run``."""

    status: str  # ok|timeout|failed|truncated
    stdout: str
    stderr: str
    duration_ms: int
    exit_code: int
    output_bytes: int
    timed_out: bool
    masked_count: int


@runtime_checkable
class SandboxRuntime(Protocol):
    """Execute a script in an isolated workspace under a policy."""

    async def run(
        self, script_path: str, input: dict, policy: "SandboxPolicy"
    ) -> RunResult: ...


# --------------------------------------------------------------------------- #
# Shared post-processing — mask + truncate + status mapping (all backends)
# --------------------------------------------------------------------------- #
def _finalize(
    stdout: str,
    stderr: str,
    exit_code: int,
    timed_out: bool,
    duration_s: float,
    policy: SandboxPolicy,
) -> RunResult:
    """Apply the safety boundaries shared by every backend and build RunResult."""
    masked_count = 0
    if policy.mask_secrets:
        stdout, n1 = mask_secrets(stdout)
        stderr, n2 = mask_secrets(stderr)
        masked_count = n1 + n2

    # Truncate to max_output_bytes (applied to stdout only; stderr kept).
    truncated = len(stdout.encode("utf-8")) > policy.max_output_bytes
    if truncated:
        stdout = stdout.encode("utf-8")[: policy.max_output_bytes].decode(
            "utf-8", errors="ignore"
        )

    # Map to RunResult.status: timeout > truncated > failed > ok.
    if timed_out:
        status = "timeout"
    elif truncated:
        status = "truncated"
    elif exit_code != 0:
        status = "failed"
    else:
        status = "ok"

    return RunResult(
        status=status,
        stdout=stdout,
        stderr=stderr,
        duration_ms=int(round((duration_s or 0) * 1000)),
        exit_code=exit_code,
        output_bytes=len(stdout.encode("utf-8")),
        timed_out=bool(timed_out),
        masked_count=masked_count,
    )


# Non-secret, platform-essential environment variables that the OS / python
# interpreter needs merely to *start* a subprocess on the host. Passing these
# into the local sandbox does NOT weaken the whitelist security boundary —
# the boundary is about not leaking arbitrary host vars (secrets, config,
# credentials). These are generic, non-sensitive process-start requirements.
_SYSTEM_REQUIRED = (
    "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP",
    "COMSPEC", "USERPROFILE", "USERNAME", "USER", "HOME", "LANG",
    "LC_ALL", "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS",
    "LOGNAME", "TERM",
)


def _restrict_env_to_whitelist(policy: "SandboxPolicy") -> dict:
    """Temporarily shrink ``os.environ`` to the whitelist + platform-required vars.

    The SDK local runtime builds the child env from ``os.environ.copy()`` and
    then merges ``spec.env`` — so the *only* way to drop non-whitelisted host
    variables is to shrink ``os.environ`` itself for the duration of the
    subprocess. We keep exactly:

      * variables named in ``policy.env_whitelist`` (the Skill's declared
        contract), and
      * a small set of non-secret platform-required vars (``_SYSTEM_REQUIRED``)
        without which python cannot even start on the host OS.

    Everything else (including ``CR_P3_VISIBLE``, ``OPENAI_API_KEY``, …) is
    dropped, so it can never leak into the sandbox. The full host env is always
    restored by the caller's ``finally`` block. CR Agent runs sandboxes
    serially (awaited), so this global mutation is safe.
    """
    _saved = dict(os.environ)
    allowed = {
        k: v for k, v in os.environ.items()
        if k in policy.env_whitelist or k in _SYSTEM_REQUIRED
    }
    os.environ.clear()
    os.environ.update(allowed)
    return _saved


def _restore_env(saved: dict) -> None:
    """Restore ``os.environ`` from a previously saved copy, tolerating vars
    that the OS cannot hold.

    On Windows, ``os.environ`` rejects any value longer than 32767 characters
    (``SetEnvironmentVariableW`` limit) — yet a process can *inherit* such a
    var from its parent. Some desktop-agent environments ship a multi-hundred-
    KB context var (e.g. ``ACC_PRODUCT_CONFIG_V3``). We must restore the host
    env after a sandbox run, but re-setting an oversized var raises
    ``ValueError``. We set each var individually and silently skip the ones the
    OS refuses, so the restore never crashes the pipeline (the un-settable var
    was already dropped during restriction and the sandbox never needed it).
    """
    os.environ.clear()
    for k, v in saved.items():
        try:
            os.environ[k] = v
        except (ValueError, OSError):
            continue


# --------------------------------------------------------------------------- #
# LocalRuntime — SDK create_local_workspace_runtime
# --------------------------------------------------------------------------- #
class LocalRuntime:
    """Sandbox backed by the SDK's local workspace runtime (process isolation).

    Creates a fresh workspace per run, stages the script directory, runs
    ``python <script>`` with the input JSON on stdin, masks + truncates
    output, and tears the workspace down. Always available (no external
    dependency) — this is the natural ``fallback`` backend.
    """

    def __init__(self, work_root: str | Path | None = None):
        self._work_root = str(work_root) if work_root else None

    def ensure_available(self) -> bool:
        """Local backend is always provisionable."""
        return True

    async def run(
        self, script_path: str, input: dict, policy: SandboxPolicy | None = None
    ) -> RunResult:
        policy = policy or SandboxPolicy()
        exec_id = f"cr-{uuid.uuid4().hex[:8]}"
        try:
            return await self._run(script_path, input, policy, exec_id)
        except Exception as exc:  # fail-safe: never crash the pipeline
            return RunResult(
                status="failed",
                stdout="",
                stderr=str(exc)[:4096],
                duration_ms=0,
                exit_code=-1,
                output_bytes=0,
                timed_out=False,
                masked_count=0,
            )

    async def _run(
        self, script_path: str, input: dict, policy: SandboxPolicy, exec_id: str
    ) -> RunResult:
        from trpc_agent_sdk.code_executors import (
            WorkspacePutFileInfo,
            WorkspaceRunProgramSpec,
            create_local_workspace_runtime,
        )

        # P1-2: enforce the env-variable whitelist for the ENTIRE sandbox
        # operation. The SDK's workspace creation, file staging, AND program
        # execution may each spawn subprocess that inherit os.environ; a single
        # oversized host var (e.g. ``ACC_PRODUCT_CONFIG_V3`` on some Windows
        # desktops — >300k chars) would otherwise break ``CreateProcess`` with
        # "the environment variable is longer than 32767 characters". Restricting
        # up front keeps every spawned process clean. Container / Cube backends
        # don't inherit the host env, so they skip this step. The full host env
        # is always restored in the ``finally`` block below.
        mgr = None
        _saved = _restrict_env_to_whitelist(policy)
        try:
            rt = create_local_workspace_runtime(
                work_root=self._work_root or str(_HERE.parent / ".ws_work")
            )
            mgr, fs, runner = rt.manager(), rt.fs(), rt.runner()
            ws = await mgr.create_workspace(exec_id)

            # Stage the whole script directory so sibling imports resolve
            # (run_checks.py does `from parse_diff import ...`).
            script = Path(script_path)
            script_dir = script.parent
            files = [
                WorkspacePutFileInfo(path=p.name, content=p.read_bytes(), mode=0o644)
                for p in sorted(script_dir.glob("*.py"))
            ]
            if files:
                await fs.put_files(ws, files)

            spec = WorkspaceRunProgramSpec(
                cmd=__import__("sys").executable,
                args=[script.name],
                env={},  # SDK merges the (already-restricted) os.environ copy
                cwd="",
                stdin=json.dumps(input, ensure_ascii=False),
                timeout=float(policy.timeout_s),
            )
            res = await runner.run_program(ws, spec)
        finally:
            _restore_env(_saved)  # restore the full host env (tolerates oversized vars)
            try:
                if mgr is not None:
                    await mgr.cleanup(exec_id)
            except Exception:
                pass

        return _finalize(
            res.stdout or "", res.stderr or "", res.exit_code,
            res.timed_out, res.duration, policy,
        )


# --------------------------------------------------------------------------- #
# ContainerRuntime — real docker isolation via SDK ContainerClient (G1 fix)
# --------------------------------------------------------------------------- #
class ContainerRuntime:
    """Sandbox backed by the SDK's docker ``ContainerClient``.

    G1 fix: this is now a real adapter (previously a ``NotImplementedError``
    stub). It provisions a docker container per run, stages the script
    directory, runs ``python <script>`` with the input JSON on stdin, and maps
    the result to :class:`RunResult` with the same five safety boundaries as
    :class:`LocalRuntime`. The container does **not** inherit the host env, so
    there is no secret-leak surface.

    If docker is unavailable (SDK/daemon missing) it raises
    :class:`RuntimeUnavailable` from :meth:`ensure_available`, letting the
    agent fall back to ``LocalRuntime`` per the skill's ``fallback`` config.
    """

    def __init__(
        self,
        policy: SandboxPolicy | None = None,
        work_root: str | Path | None = None,
        image: str | None = None,
    ):
        self._policy = policy or SandboxPolicy()
        self._work_root = work_root
        self._image = image

    def ensure_available(self) -> bool:
        """Lightweight probe: is the docker backend usable here?"""
        try:
            import docker  # noqa: F401  (import fails if SDK extra absent)
        except Exception as exc:
            raise RuntimeUnavailable(f"docker SDK not installed: {exc}") from exc
        try:
            client = docker.from_env()
            client.ping()
        except Exception as exc:
            raise RuntimeUnavailable(f"docker daemon not reachable: {exc}") from exc
        return True

    async def run(
        self, script_path: str, input: dict, policy: SandboxPolicy | None = None
    ) -> RunResult:
        policy = policy or self._policy
        exec_id = f"cr-{uuid.uuid4().hex[:8]}"
        try:
            return await self._run(script_path, input, policy, exec_id)
        except RuntimeUnavailable:
            raise  # let the agent's fallback logic decide
        except Exception as exc:  # fail-safe: never crash the pipeline
            return RunResult(
                status="failed",
                stdout="",
                stderr=str(exc)[:4096],
                duration_ms=0,
                exit_code=-1,
                output_bytes=0,
                timed_out=False,
                masked_count=0,
            )

    async def _run(
        self, script_path: str, input: dict, policy: SandboxPolicy, exec_id: str
    ) -> RunResult:
        try:
            from trpc_agent_sdk.code_executors.container._container_cli import (
                CommandArgs,
                ContainerClient,
                ContainerConfig,
            )
        except Exception as exc:
            raise RuntimeUnavailable(f"container SDK unavailable: {exc}") from exc

        image = self._image or os.environ.get("CR_CONTAINER_IMAGE") or "python:3-slim"
        network_mode = os.environ.get("CR_CONTAINER_NETWORK", "none")
        workdir = "/workspace"
        client = ContainerClient(
            ContainerConfig(
                image=image,
                host_config={
                    "working_dir": workdir,
                    "network_mode": network_mode,
                    "auto_remove": True,
                    "command": ["tail", "-f", "/dev/null"],
                    "stdin": True,
                },
            )
        )
        try:
            # Stage the whole script directory into the container. We transfer
            # each file as base64 on the command line — deliberately NOT via the
            # SDK's stdin exec path, which has a socket-reuse bug in the vendored
            # ContainerClient. The no-stdin `exec_run` branch is robust. The
            # container does not inherit the host env, so no secrets leak in.
            script = Path(script_path)
            script_dir = script.parent
            for p in sorted(script_dir.glob("*.py")):
                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                stage = await client.exec_run(
                    ["sh", "-c",
                     f"mkdir -p {workdir} && echo {b64} | base64 -d > {workdir}/{p.name}"],
                    CommandArgs(timeout=30),
                )
                if stage.exit_code != 0:
                    raise RuntimeError(f"staging {p.name} failed: {stage.stderr}")

            # Stage the JSON input as a file too (avoids the stdin exec bug).
            in_b64 = base64.b64encode(
                json.dumps(input, ensure_ascii=False).encode("utf-8")
            ).decode("ascii")
            stage_in = await client.exec_run(
                ["sh", "-c", f"echo {in_b64} | base64 -d > {workdir}/_input.json"],
                CommandArgs(timeout=30),
            )
            if stage_in.exit_code != 0:
                raise RuntimeError(f"staging input failed: {stage_in.stderr}")

            # Run the checker, feeding the staged input via a stdin redirect
            # inside the container (no docker-level stdin involved).
            started = time.monotonic()
            res = await client.exec_run(
                ["sh", "-c",
                 f"python {workdir}/{script.name} < {workdir}/_input.json"],
                CommandArgs(timeout=float(policy.timeout_s)),
            )
            duration = time.monotonic() - started
            return _finalize(
                res.stdout or "", res.stderr or "", res.exit_code,
                res.is_timeout, duration, policy,
            )
        finally:
            try:
                client._cleanup_container()  # stop + remove
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# CubeRuntime — real remote Cube/E2B sandbox via SDK CubeSandboxClient
# --------------------------------------------------------------------------- #
class CubeRuntime:
    """Sandbox backed by the SDK's remote Cube/E2B ``CubeSandboxClient``.

    G1 fix: this is now a real adapter (previously a stub). It opens a remote
    sandbox, uploads the script directory, runs ``python <script>`` with the
    input JSON on stdin, and maps the structured result to :class:`RunResult`.
    The remote sandbox is destroyed after each run.

    Requires the ``[cube]`` extra and credentials. If the extra is missing or
    credentials are unset, :meth:`ensure_available` raises
    :class:`RuntimeUnavailable` so the agent falls back to ``LocalRuntime``.
    """

    def __init__(
        self,
        policy: SandboxPolicy | None = None,
        work_root: str | Path | None = None,
    ):
        self._policy = policy or SandboxPolicy()
        self._work_root = work_root

    def ensure_available(self) -> bool:
        """Lightweight probe: is the cube backend configured here?"""
        try:
            from trpc_agent_sdk.code_executors.cube._sandbox import (  # noqa: F401
                create_cube_sandbox_client,
            )
        except Exception as exc:
            raise RuntimeUnavailable(f"cube SDK (e2b) unavailable: {exc}") from exc
        if not os.environ.get("CR_CUBE_API_KEY"):
            raise RuntimeUnavailable("cube backend not configured (CR_CUBE_API_KEY unset)")
        if not (os.environ.get("CR_CUBE_TEMPLATE") or os.environ.get("CR_CUBE_SANDBOX_ID")):
            raise RuntimeUnavailable(
                "cube backend not configured (need CR_CUBE_TEMPLATE or CR_CUBE_SANDBOX_ID)"
            )
        return True

    async def run(
        self, script_path: str, input: dict, policy: SandboxPolicy | None = None
    ) -> RunResult:
        policy = policy or self._policy
        exec_id = f"cr-{uuid.uuid4().hex[:8]}"
        try:
            return await self._run(script_path, input, policy, exec_id)
        except RuntimeUnavailable:
            raise
        except Exception as exc:  # fail-safe: never crash the pipeline
            return RunResult(
                status="failed",
                stdout="",
                stderr=str(exc)[:4096],
                duration_ms=0,
                exit_code=-1,
                output_bytes=0,
                timed_out=False,
                masked_count=0,
            )

    async def _run(
        self, script_path: str, input: dict, policy: SandboxPolicy, exec_id: str
    ) -> RunResult:
        try:
            from trpc_agent_sdk.code_executors.cube._sandbox import (
                create_cube_sandbox_client,
            )
            from trpc_agent_sdk.code_executors.cube._types import CubeClientConfig
        except Exception as exc:
            raise RuntimeUnavailable(f"cube SDK unavailable: {exc}") from exc

        workdir = "/home/user"
        cfg = CubeClientConfig(
            template=os.environ.get("CR_CUBE_TEMPLATE"),
            api_url=os.environ.get("CR_CUBE_API_URL"),
            api_key=os.environ.get("CR_CUBE_API_KEY"),
            sandbox_id=os.environ.get("CR_CUBE_SANDBOX_ID") or None,
            execute_timeout=float(policy.timeout_s),
            idle_timeout=300,
        )
        client = await create_cube_sandbox_client(cfg)
        try:
            # Upload the script directory.
            script = Path(script_path)
            script_dir = script.parent
            for p in sorted(script_dir.glob("*.py")):
                await client.write_file_bytes(f"{workdir}/{p.name}", p.read_bytes())

            started = time.monotonic()
            res = await client.commands_run(
                f"python {workdir}/{script.name}",
                cwd=workdir,
                env={},  # remote sandbox never inherits host secrets
                stdin=json.dumps(input, ensure_ascii=False).encode("utf-8"),
                timeout=float(policy.timeout_s),
            )
            duration = time.monotonic() - started
            return _finalize(
                res.stdout or "", res.stderr or "", res.exit_code,
                res.timed_out, duration, policy,
            )
        finally:
            try:
                await client.destroy()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Runtime selection — honor default_runtime / fallback (G1 fix)
# --------------------------------------------------------------------------- #
def select_runtime(
    kind: str,
    policy: SandboxPolicy | None = None,
    work_root: str | Path | None = None,
    image: str | None = None,
) -> "SandboxRuntime":
    """Construct a single sandbox runtime by kind (local|container|cube)."""
    kind = (kind or "local").lower()
    if kind in ("container", "docker"):
        return ContainerRuntime(policy=policy, work_root=work_root, image=image)
    if kind == "cube":
        return CubeRuntime(policy=policy, work_root=work_root)
    return LocalRuntime(work_root=work_root)


def build_runtime_with_fallback(
    default_kind: str | None,
    fallback_kind: str | None,
    policy: SandboxPolicy | None = None,
    work_root: str | Path | None = None,
    image: str | None = None,
) -> tuple["SandboxRuntime", str]:
    """Pick the sandbox runtime honoring ``default_runtime``/``fallback``.

    Tries ``default_kind`` first (probing availability); on
    :class:`RuntimeUnavailable` (docker/cube absent) it transparently falls
    back to ``fallback_kind`` (typically ``local``). Returns
    ``(runtime, actual_kind)`` so the caller records which runtime actually
    executed. If even the fallback is unavailable the raw error propagates —
    the agent's outer handler then degrades to ``FakeRunner``.
    """
    tried: list[str] = []
    for kind in (default_kind, fallback_kind):
        if not kind:
            continue
        kind = kind.lower()
        if kind in tried:
            continue
        tried.append(kind)
        try:
            rt = select_runtime(kind, policy, work_root, image)
            rt.ensure_available()
            logger.info("sandbox runtime selected: %s", kind)
            return rt, kind
        except RuntimeUnavailable as e:
            logger.warning(
                "sandbox runtime %r unavailable (%s); trying fallback", kind, e
            )
            continue
    raise RuntimeUnavailable(
        f"No sandbox runtime available for default={default_kind!r} "
        f"fallback={fallback_kind!r}"
    )
