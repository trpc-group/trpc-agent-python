# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandbox execution on top of trpc_agent_sdk.code_executors.

Runtime selection (issue requirement 2):
  * ``container`` — Docker-based ContainerWorkspaceRuntime; the PRODUCTION
    default documented in the README (native filesystem/network isolation).
  * ``cube``      — Cube/E2B cloud sandbox (imported lazily; needs an E2B key).
  * ``local``     — development fallback ONLY (used by tests / dry-run on
    hosts without Docker), hardened with an environment whitelist because the
    stock LocalProgramRunner inherits the full host environment.

Safety guarantees (issue requirement 7): per-run timeout, stdout/stderr size
cap, env whitelist, secret-redacted excerpts, and a ``SandboxRunOutcome`` that
records failures — :meth:`SandboxExecutor.run_checks` NEVER raises, so a
sandbox crash cannot kill the review task (acceptance criterion 4).
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import List
from typing import Optional

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import WorkspacePutFileInfo
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.code_executors import WorkspaceRunResult
from trpc_agent_sdk.code_executors import WorkspaceStageOptions
from trpc_agent_sdk.code_executors.local import LocalProgramRunner
from trpc_agent_sdk.code_executors.local import LocalWorkspaceRuntime

from .config import SandboxConfig
from .config import SKILL_NAME
from .config import SKILLS_ROOT

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_TIMEOUT = "timeout"
STATUS_ERROR = "error"
STATUS_BLOCKED = "blocked"

#: Workspace-layout env vars the SDK runner injects; always kept.
_WORKSPACE_ENV_KEYS = frozenset({
    "WORKSPACE_DIR", "SKILLS_DIR", "WORK_DIR", "OUTPUT_DIR", "RUN_DIR", "SKILL_NAME",
})

_SKILL_REL_DIR = f"skills/{SKILL_NAME}"
_DIFF_JSON_REL = "work/inputs/diff.json"
_FILES_REL_DIR = "work/inputs/files"
_FINDINGS_REL = "out/findings.json"


class EnvWhitelistLocalProgramRunner(LocalProgramRunner):
    """LocalProgramRunner that drops every non-whitelisted host env var.

    The stock local runner builds the child environment from
    ``os.environ.copy()`` — fine for trusted skills, but a review sandbox must
    not leak host secrets (API keys, tokens) into analyzed code. Container and
    Cube runtimes isolate the environment natively; this subclass gives the
    local dev-fallback the same property.
    """

    def __init__(self, allowed_env: frozenset, extra_env: Optional[Dict[str, str]] = None) -> None:
        super().__init__()
        self._allowed_env = frozenset(allowed_env)
        self._extra_env = dict(extra_env or {})

    def _build_program_env(self, ws, spec) -> Dict[str, str]:
        env = super()._build_program_env(ws, spec)
        keep = self._allowed_env | _WORKSPACE_ENV_KEYS | set((spec.env or {}).keys())
        filtered = {key: value for key, value in env.items() if key in keep}
        filtered.update(self._extra_env)
        return filtered


def create_sandbox_runtime(cfg: SandboxConfig) -> BaseWorkspaceRuntime:
    """Instantiate the workspace runtime selected by ``cfg.runtime_kind``."""
    if cfg.runtime_kind == "local":
        runtime = LocalWorkspaceRuntime(work_root=cfg.work_root)
        # Swap in the hardened runner (see class docstring).
        runtime._runner = EnvWhitelistLocalProgramRunner(cfg.env_whitelist)  # pylint: disable=protected-access
        return runtime
    if cfg.runtime_kind == "container":
        from trpc_agent_sdk.code_executors import ContainerConfig
        from trpc_agent_sdk.code_executors import create_container_workspace_runtime
        return create_container_workspace_runtime(container_config=ContainerConfig(image=cfg.container_image))
    if cfg.runtime_kind == "cube":
        # Lazy import: the cube subpackage imports e2b_code_interpreter at module scope.
        from trpc_agent_sdk.code_executors.cube import create_cube_workspace_runtime
        return create_cube_workspace_runtime()
    raise ValueError(f"unknown sandbox runtime kind: {cfg.runtime_kind!r}")


@dataclass
class SandboxRunOutcome:
    """Result envelope of one sandbox run attempt."""

    status: str  # ok | failed | timeout | error
    result: Optional[WorkspaceRunResult] = None
    stdout: str = ""
    stderr: str = ""
    output_truncated: bool = False
    findings_payload: Optional[dict] = None
    error_type: str = ""
    cmd: str = ""
    args: list = field(default_factory=list)
    duration_ms: float = 0.0


def _cap_output(text: str, max_bytes: int) -> tuple:
    """Truncate ``text`` to ``max_bytes`` (UTF-8), marking truncation."""
    if text is None:
        return "", False
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text, False
    clipped = encoded[:max_bytes].decode("utf-8", errors="replace")
    return clipped + "\n…[output truncated]", True


class SandboxExecutor:
    """Stages the code-review skill into an isolated workspace and runs checks."""

    def __init__(self, runtime: BaseWorkspaceRuntime, cfg: SandboxConfig,
                 skill_root: str = "") -> None:
        self._runtime = runtime
        self._cfg = cfg
        self._skill_root = skill_root or os.path.join(SKILLS_ROOT, SKILL_NAME)

    @property
    def check_script_host_path(self) -> str:
        return os.path.join(self._skill_root, "scripts", "run_checks.py")

    @property
    def check_cmd(self) -> str:
        """Interpreter of the check run: the host interpreter for the local
        runtime (``python3`` may not exist on e.g. Windows hosts), plain
        ``python3`` inside container/cube runtimes."""
        if self._cfg.runtime_kind == "local":
            return sys.executable or "python3"
        return "python3"

    def build_check_args(self, file_contents: Optional[Dict[str, str]] = None) -> List[str]:
        """Complete argv (after the interpreter) of the check run — exposed so
        the governance gate inspects exactly what will execute."""
        args = [
            f"{_SKILL_REL_DIR}/scripts/run_checks.py",
            _DIFF_JSON_REL,
            _FINDINGS_REL,
        ]
        if file_contents:
            args += ["--files-dir", _FILES_REL_DIR]
        if self._cfg.force_fail:
            args.append("--force-fail")
        return args

    async def run_checks(self, task_id: str, changeset_json: dict,
                         file_contents: Optional[Dict[str, str]] = None) -> SandboxRunOutcome:
        """Run the skill's ``run_checks.py`` over a parsed changeset. Never raises."""
        args = self.build_check_args(file_contents)

        exec_id = f"cr-{task_id[:8]}-{uuid.uuid4().hex[:8]}"
        manager = self._runtime.manager()
        fs = self._runtime.fs()
        runner = self._runtime.runner()
        outcome = SandboxRunOutcome(status=STATUS_ERROR, cmd=self.check_cmd, args=list(args))
        try:
            ws = await manager.create_workspace(exec_id)
            try:
                # Stage the skill directory. The SDK's SkillStager requires a
                # full InvocationContext (agent invocation); this deterministic
                # pipeline stages the same tree directly via the workspace FS,
                # which is exactly what the stager does internally.
                await fs.stage_directory(ws, self._skill_root, _SKILL_REL_DIR,
                                         WorkspaceStageOptions(mode="copy", read_only=True))
                put_files = [
                    WorkspacePutFileInfo(
                        path=_DIFF_JSON_REL,
                        content=json.dumps({"changeset": changeset_json}).encode("utf-8"),
                        mode=0o644,
                    )
                ]
                for rel_path, content in (file_contents or {}).items():
                    safe_rel = rel_path.replace("\\", "/").lstrip("/")
                    if ".." in safe_rel.split("/"):
                        continue
                    put_files.append(
                        WorkspacePutFileInfo(path=f"{_FILES_REL_DIR}/{safe_rel}",
                                             content=content.encode("utf-8"), mode=0o644))
                await fs.put_files(ws, put_files)

                spec = WorkspaceRunProgramSpec(
                    cmd=self.check_cmd,
                    args=args,
                    env={"SKILL_NAME": SKILL_NAME},
                    cwd="",
                    timeout=self._cfg.timeout_sec,
                )
                result = await runner.run_program(ws, spec)
                outcome.result = result
                outcome.duration_ms = (result.duration or 0.0) * 1000.0
                outcome.stdout, truncated_out = _cap_output(result.stdout, self._cfg.max_output_bytes)
                outcome.stderr, truncated_err = _cap_output(result.stderr, self._cfg.max_output_bytes)
                outcome.output_truncated = truncated_out or truncated_err

                if result.timed_out:
                    outcome.status = STATUS_TIMEOUT
                    outcome.error_type = "SandboxTimeout"
                    return outcome
                if result.exit_code != 0:
                    outcome.status = STATUS_FAILED
                    outcome.error_type = "SandboxNonZeroExit"
                    return outcome

                payload = await self._collect_findings(fs, ws)
                if payload is None:
                    outcome.status = STATUS_FAILED
                    outcome.error_type = "MissingFindingsOutput"
                    return outcome
                outcome.findings_payload = payload
                outcome.status = STATUS_OK
                return outcome
            finally:
                try:
                    await manager.cleanup(exec_id)
                except Exception:  # pylint: disable=broad-except
                    pass  # cleanup failure must not mask the run outcome
        except Exception as ex:  # pylint: disable=broad-except
            # Contract: sandbox problems surface as data, never as exceptions.
            outcome.status = STATUS_ERROR
            outcome.error_type = type(ex).__name__
            outcome.stderr = (outcome.stderr + f"\n{type(ex).__name__}: {ex}").strip()
            return outcome

    async def _collect_findings(self, fs, ws) -> Optional[dict]:
        """Fetch out/findings.json from the workspace; host-read fallback when truncated."""
        try:
            files = await fs.collect(ws, [_FINDINGS_REL])
            for code_file in files:
                if code_file.truncated:
                    break
                return json.loads(code_file.content)
        except Exception:  # pylint: disable=broad-except
            pass
        # Local-runtime fallback: read straight from the workspace dir.
        host_path = os.path.join(ws.path, *_FINDINGS_REL.split("/"))
        if os.path.isfile(host_path):
            try:
                with open(host_path, "r", encoding="utf-8") as fh:
                    return json.load(fh)
            except (OSError, json.JSONDecodeError):
                return None
        return None
