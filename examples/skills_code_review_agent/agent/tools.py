# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool wiring for the code-review agent.

The review runs as the **code-review Skill**, through the framework's own Skills mechanism: the
agent calls ``skill_load`` to read ``SKILL.md`` and its rule docs, then ``skill_run`` to execute the
skill's ``scripts/run_checks.py`` inside a workspace sandbox against the staged changed files. Two
host-side FunctionTools bracket that skill call:

* ``stage_review_input`` — parse a unified diff and materialize its post-change files into a host
  directory that ``skill_run`` mounts as its ``inputs/`` (the framework stages inputs by
  ``host://`` reference; there is no inline-content input form).
* ``finalize_review`` — take the skill's ``findings.json``, dedup/denoise it, persist it and render
  the report.

The workspace runtime defaults to **container**: issue #92 req 2 allows a local runtime only as a
development fallback, never as the default production path.
"""
from __future__ import annotations

import atexit
import json
import os
import shutil
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import create_container_workspace_runtime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import ENV_SKILLS_ROOT
from trpc_agent_sdk.skills import SkillToolSet
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.skills.tools import CopySkillStager
from trpc_agent_sdk.tools import FunctionTool

from pipeline import engine, report as report_mod
from pipeline.policy import ENV_ALLOWLIST
from pipeline.types import DiffSummary

from .filter import ReviewGuardFilter

SKILL_NAME = "code-review"

#: Repository-root ``skills/`` directory that holds ``code-review/SKILL.md``.
DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[3] / "skills"

#: Scanner image built from ``skills/code-review/Dockerfile``.
DEFAULT_SANDBOX_IMAGE = "cr-scanners:latest"

#: Only these command names may run in the sandbox; the framework rejects shell metacharacters too.
ALLOWED_SANDBOX_CMDS = ["python", "python3"]

SANDBOX_TIMEOUT_SEC = int(os.getenv("REVIEW_SANDBOX_TIMEOUT_SEC", "120"))


def skills_root() -> str:
    """Where the skill repository scans from (the SDK's ``SKILLS_ROOT`` env var wins)."""
    return os.getenv(ENV_SKILLS_ROOT) or str(DEFAULT_SKILLS_ROOT)


def create_workspace_runtime(kind: str = "container") -> BaseWorkspaceRuntime:
    """Build the runtime the skill executes in.

    ``container`` is the default production isolation. ``local`` is the development fallback the
    issue permits; it is never selected implicitly.
    """
    if kind == "container":
        from trpc_agent_sdk.code_executors.container import ContainerConfig
        image = os.getenv("REVIEW_SANDBOX_IMAGE", DEFAULT_SANDBOX_IMAGE)
        return create_container_workspace_runtime(container_config=ContainerConfig(image=image))
    if kind == "local":
        return create_local_workspace_runtime()
    raise ValueError(f"unknown workspace runtime {kind!r} (expected 'container' or 'local')")


class ReviewSkillToolSet(SkillToolSet):
    """The Skills toolset, narrowed to the two tools this agent is allowed to use.

    ``SkillToolSet`` also hands out ``skill_exec``, ``workspace_exec``, ``workspace_write_stdin`` and
    ``workspace_kill_session`` — arbitrary command execution constructed *without* filters
    (``_toolset.py`` builds them with no ``filters=``). That would leave issue #92's requirement 8
    unmet no matter what we attach to ``skill_run``: the model could simply use another tool.

    The inherited ``tool_filter`` parameter cannot do this — ``SkillToolSet.get_tools`` never calls
    ``_is_tool_selected`` — so the narrowing happens here. ``super().get_tools()`` must still run: it
    is what attaches the skill registry/repository to the agent context.
    """

    _EXPOSED = frozenset({"skill_load", "skill_run"})

    async def get_tools(self, invocation_context=None):
        tools = await super().get_tools(invocation_context)
        return [t for t in tools if t.name in self._EXPOSED]


def _sandbox_env_overrides() -> dict[str, str]:
    """Blank every non-whitelisted variable the parent process would otherwise leak into the sandbox.

    The local workspace runtime builds a scanner's environment from ``os.environ.copy()``, so without
    this the scanners would inherit ``TRPC_AGENT_API_KEY`` and everything else — issue #92's
    requirement 7 asks for the opposite. Only the *parent* environment is blanked, so the runtime's
    own ``$WORK_DIR`` / ``$OUTPUT_DIR`` / ``$SKILLS_DIR`` injections survive.
    """
    return {key: "" for key in os.environ if key not in ENV_ALLOWLIST}


def create_skill_tool_set(runtime: str = "container") -> tuple[SkillToolSet, BaseSkillRepository]:
    """The framework's Skills toolset, bound to a real workspace runtime.

    ``create_default_skill_repository`` falls back to a *local* runtime when none is passed, so the
    runtime is always constructed explicitly here.

    The guard Filter and the command allowlist are attached to ``skill_run`` (issue #92 req 8:
    high-risk actions are gated *before* sandbox execution). ``require_skill_loaded`` makes
    ``skill_load`` a precondition of ``skill_run``, so the skill's rules genuinely enter the agent's
    context instead of the script being invoked blind.

    ``CopySkillStager`` (not the ``LinkSkillStager`` default) keeps the sandbox from writing back
    into the repository through the staged symlinks.
    """
    workspace_runtime = create_workspace_runtime(runtime)
    repository = create_default_skill_repository(
        skills_root(),
        workspace_runtime=workspace_runtime,
        use_cached_repository=True,
    )
    # `filters`, `require_skill_loaded` and `allowed_cmds` are declared parameters of
    # SkillRunTool.__init__, so they work passed flat. `timeout` and `env` are NOT: SkillRunTool
    # reads those out of a nested `run_tool_kwargs` dict, and anything else lands in **kwargs and is
    # silently discarded. Passing a timeout flat looks like it works and does nothing.
    toolset = ReviewSkillToolSet(
        repository=repository,
        skill_stager=CopySkillStager(),
        filters=[ReviewGuardFilter()],
        require_skill_loaded=True,
        allowed_cmds=ALLOWED_SANDBOX_CMDS,
        run_tool_kwargs={
            "timeout": SANDBOX_TIMEOUT_SEC,
            "env": _sandbox_env_overrides(),
        },
    )
    return toolset, repository


@dataclass
class _StagedInput:
    """A materialized review input, held between ``stage_review_input`` and ``finalize_review``."""

    task_id: str
    host_dir: str
    summary: DiffSummary
    source_type: str
    source_ref: str
    started: float


#: Bounded so a long-running server neither grows without limit nor keeps every scan's temp dir on
#: disk; evicting a task also removes the files it staged.
_STAGED_LIMIT = 16
_STAGED: "OrderedDict[str, _StagedInput]" = OrderedDict()


def _remember(staged: _StagedInput) -> None:
    _STAGED[staged.task_id] = staged
    while len(_STAGED) > _STAGED_LIMIT:
        _, evicted = _STAGED.popitem(last=False)
        shutil.rmtree(evicted.host_dir, ignore_errors=True)


def _discard_all() -> None:
    while _STAGED:
        _, staged = _STAGED.popitem()
        shutil.rmtree(staged.host_dir, ignore_errors=True)


atexit.register(_discard_all)


def stage_review_input(diff_text: str) -> dict[str, Any]:
    """Parse a unified diff and materialize its post-change files for the sandbox to scan.

    Args:
        diff_text: the unified diff to review.

    Returns:
        The task id, the host directory to mount into the sandbox, and the changed-file summary.
    """
    summary, host_dir = engine.materialize_diff(diff_text)
    task_id = engine.new_task_id()
    _remember(
        _StagedInput(task_id=task_id,
                     host_dir=host_dir,
                     summary=summary,
                     source_type="diff_file",
                     source_ref=f"{summary.files_changed} file(s)",
                     started=time.monotonic()))
    return {
        "task_id": task_id,
        "host_dir": host_dir,
        "files_changed": summary.files_changed,
        "files": [f.path for f in summary.files],
        "skill_run_hint": {
            "skill": SKILL_NAME,
            "command": "python scripts/run_checks.py --target inputs --out out/findings.json",
            "inputs": [{
                "src": f"host://{host_dir}",
                "dst": "inputs",
                "mode": "copy"
            }],
            "output_files": ["out/findings.json"],
        },
    }


async def finalize_review(task_id: str,
                          findings_json: str,
                          exit_code: int = 0,
                          duration_ms: int = 0) -> dict[str, Any]:
    """Deduplicate, persist and render the review from the skill's findings.

    Args:
        task_id: the id returned by stage_review_input.
        findings_json: the verbatim contents of the skill's out/findings.json.
        exit_code: the skill_run exit code (0 is expected; non-zero means the harness failed).
        duration_ms: the skill_run duration in milliseconds, for the monitoring section.

    Returns:
        The findings summary and severity statistics, or a structured error to retry on.
    """
    staged = _STAGED.get(task_id)
    if staged is None:
        return {"error": f"unknown task_id {task_id!r} — call stage_review_input first"}

    payload = findings_json
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        # Models routinely hand a dict to a string-typed parameter; json.loads then raises TypeError,
        # not JSONDecodeError, and an uncaught TypeError would 500 the tool instead of prompting a retry.
        except (TypeError, json.JSONDecodeError) as exc:
            return {
                "error": (f"findings_json is not valid JSON ({exc}); pass the verbatim contents of "
                          f"out/findings.json returned by skill_run")
            }
    if not isinstance(payload, dict):
        return {"error": "findings_json must be the findings.json object"}

    result = engine.assemble_from_skill_findings(task_id=staged.task_id,
                                                 summary=staged.summary,
                                                 payload=payload,
                                                 source_type=staged.source_type,
                                                 source_ref=staged.source_ref,
                                                 started=staged.started,
                                                 skill_run_result={
                                                     "exit_code": exit_code,
                                                     "duration_ms": duration_ms,
                                                 })

    out_dir = Path(os.getenv("REVIEW_OUT_DIR", "."))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "review_report.json").write_text(report_mod.render_json(result.report), encoding="utf-8")
    (out_dir / "review_report.md").write_text(report_mod.render_md(result.report), encoding="utf-8")

    persisted = False
    if os.getenv("REVIEW_NO_DB") != "1":
        from storage.dao import ReviewStore
        store = ReviewStore(os.getenv("REVIEW_DB_URL", "sqlite+aiosqlite:///./code_review.db"))
        await store.init()
        try:
            await store.persist(result)
            persisted = True
        finally:
            await store.close()

    return {
        "task_id": result.task_id,
        "summary": result.report.findings_summary,
        "severity": result.report.severity_stats,
        "needs_human_review": len(result.report.human_review),
        "persisted": persisted,
        "report_dir": str(out_dir.resolve()),
    }


def build_host_tools() -> list[FunctionTool]:
    """The two host-side tools that bracket the skill call."""
    return [FunctionTool(stage_review_input), FunctionTool(finalize_review)]
