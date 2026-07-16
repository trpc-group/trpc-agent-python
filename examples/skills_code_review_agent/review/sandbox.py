# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox execution of skill scripts via SDK workspace runtimes."""
import os
import shutil
import sys
from dataclasses import dataclass

from trpc_agent_sdk.code_executors import (BaseWorkspaceRuntime, WorkspacePutFileInfo,
                                           WorkspaceRunProgramSpec,
                                           WorkspaceStageOptions,
                                           create_container_workspace_runtime,
                                           create_local_workspace_runtime)

SKILL_WS_DIR = "skills/code-review"
DIFF_WS_PATH = "work/inputs/changes.diff"
SANDBOX_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin",
               "HOME": "/tmp", "LANG": "C.UTF-8"}

RUNTIME_LOCAL = "local"
RUNTIME_CONTAINER = "container"
RUNTIME_CUBE = "cube"


async def create_runtime(kind: str):
    """Create a workspace runtime. Container is the production default;
    local is a development-only fallback; cube needs env credentials."""
    if kind == RUNTIME_LOCAL:
        return create_local_workspace_runtime()
    if kind == RUNTIME_CONTAINER:
        return create_container_workspace_runtime()
    if kind == RUNTIME_CUBE:
        from trpc_agent_sdk.code_executors.cube import (CubeClientConfig,
                                                        CubeWorkspaceRuntimeConfig,
                                                        create_cube_sandbox_client,
                                                        create_cube_workspace_runtime)
        if not os.getenv("CUBE_API_KEY") and not os.getenv("E2B_API_KEY"):
            raise ValueError("cube runtime requires CUBE_API_KEY/E2B_API_KEY; "
                             "see examples/skills_with_cube")
        cfg = CubeClientConfig(
            execute_timeout=float(os.getenv("CUBE_EXECUTE_TIMEOUT", "60")),
            idle_timeout=int(os.getenv("CUBE_IDLE_TIMEOUT", "600")),
            auto_recover=True)
        client = await create_cube_sandbox_client(cfg)
        return create_cube_workspace_runtime(sandbox_client=client,
                                             execute_timeout=cfg.execute_timeout,
                                             workspace_cfg=CubeWorkspaceRuntimeConfig())
    raise ValueError(f"unknown runtime {kind!r}; expected local|container|cube")


@dataclass
class SandboxRunOutcome:
    """Result of one sandboxed script execution."""

    script: str
    status: str  # ok | failed | timeout | error
    exit_code: int
    duration_ms: int
    timed_out: bool
    stdout: str
    stderr: str
    error_type: str = ""
    truncated: bool = False


class SandboxSession:
    """One workspace: staged code-review skill + staged diff + script runs."""

    def __init__(self, runtime: BaseWorkspaceRuntime, skill_root: str, timeout_sec: float = 60.0,
                 max_output_bytes: int = 262144):
        self._runtime = runtime
        self._skill_root = skill_root
        self._timeout = timeout_sec
        self._max_output = max_output_bytes
        self._ws = None
        self._exec_id = ""

    async def open(self, exec_id: str) -> None:
        if self._ws is not None:
            raise RuntimeError("SandboxSession is already open; call close() first")
        self._exec_id = exec_id
        manager = self._runtime.manager(None)
        self._ws = await manager.create_workspace(exec_id, None)
        fs = self._runtime.fs(None)
        await fs.stage_directory(self._ws, self._skill_root, SKILL_WS_DIR,
                                 WorkspaceStageOptions(mode="copy"), None)

    async def put_diff(self, diff_text: str) -> None:
        fs = self._runtime.fs(None)
        await fs.put_files(self._ws, [WorkspacePutFileInfo(
            path=DIFF_WS_PATH, content=diff_text.encode("utf-8"), mode=0o644)], None)

    def _truncate(self, text: str):
        if len(text.encode("utf-8", errors="replace")) <= self._max_output:
            return text, False
        return text.encode("utf-8", errors="replace")[:self._max_output].decode(
            "utf-8", errors="replace"), True

    def _effective_env(self) -> dict:
        """Whitelist stays PATH/HOME/LANG only; host python3 dir is appended as a
        dev fallback for hosts without /usr/bin/python3 (e.g. Nix); harmless
        nonexistent entry inside containers."""
        env = dict(SANDBOX_ENV)
        python3_dir = os.path.dirname(shutil.which("python3") or sys.executable)
        if python3_dir and python3_dir not in env["PATH"].split(":"):
            env["PATH"] = f"{env['PATH']}:{python3_dir}"
        return env

    async def run_script(self, script_name: str,
                         args: tuple = (DIFF_WS_PATH,)) -> SandboxRunOutcome:
        """Run one skill script under `env -i` (environment whitelist)."""
        env = self._effective_env()
        env_args = [f"{k}={v}" for k, v in env.items()]
        argv = ["-i", *env_args, "python3",
                f"{SKILL_WS_DIR}/scripts/{script_name}", *args]
        spec = WorkspaceRunProgramSpec(cmd="env", args=argv, env={}, cwd=".",
                                       timeout=self._timeout)
        try:
            ret = await self._runtime.runner(None).run_program(self._ws, spec, None)
        except Exception as ex:  # noqa: BLE001 - sandbox failure must not crash the review
            return SandboxRunOutcome(script=script_name, status="error", exit_code=-1,
                                     duration_ms=0, timed_out=False, stdout="",
                                     stderr=str(ex)[:1024], error_type=type(ex).__name__)
        stdout, t1 = self._truncate(ret.stdout or "")
        stderr, t2 = self._truncate(ret.stderr or "")
        if ret.timed_out:
            status = "timeout"
        elif ret.exit_code == 0:
            status = "ok"
        else:
            status = "failed"
        return SandboxRunOutcome(script=script_name, status=status,
                                 exit_code=ret.exit_code,
                                 duration_ms=int(ret.duration * 1000),
                                 timed_out=ret.timed_out, stdout=stdout,
                                 stderr=stderr,
                                 error_type="timeout" if ret.timed_out else "",
                                 truncated=t1 or t2)

    async def close(self) -> None:
        if self._ws is None:
            return
        try:
            await self._runtime.manager(None).cleanup(self._exec_id, None)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
        self._ws = None
