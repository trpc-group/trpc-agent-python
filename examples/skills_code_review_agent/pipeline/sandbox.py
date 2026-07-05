# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sandboxed execution of the review scanners (issue #92, requirement 4 & slice 2).

Runs the skill's ``run_checks.py`` outside the review process, with a timeout and an output-size cap,
and records a ``SandboxRunResult`` for every run — including timeouts and failures — so one bad run
degrades a source without crashing the task.

Two runtimes:
- ``run_local``: subprocess execution (the dev fallback the issue permits) — real process boundary,
  timeout and output cap; verified without Docker.
- ``run_container``: production isolation via the framework's Container workspace runtime; requires
  Docker and the scanner image (see ``skills/code-review/Dockerfile``).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .types import Finding, SandboxRunResult

if TYPE_CHECKING:
    from .policy import ReviewPolicy

_SKILL_SCRIPT = Path(__file__).resolve().parents[3] / "skills" / "code-review" / "scripts" / "run_checks.py"
DEFAULT_TIMEOUT_SEC = 60.0
MAX_OUTPUT_BYTES = 1_048_576  # 1 MiB per stream


def _gate(policy: "ReviewPolicy | None", cmd: list[str], scan_dir: str, timeout: float) -> SandboxRunResult | None:
    """Return a blocked result if the policy refuses the action, else None (allowed to run)."""
    if policy is None:
        return None
    decision = policy.evaluate(command=" ".join(cmd), touched_paths=[scan_dir], budget_sec=timeout)
    if decision.allowed:
        return None
    return SandboxRunResult(script="run_checks.py",
                            exit_code=0,
                            duration_sec=0.0,
                            timed_out=False,
                            stdout_bytes=0,
                            stderr_bytes=0,
                            blocked=True,
                            block_reason=decision.reason,
                            block_category=decision.category)


def _truncate(text: str | bytes | None, cap: int) -> tuple[str, int]:
    """Return (possibly-truncated text, original byte length). The cap bounds what we persist."""
    if text is None:
        return "", 0
    raw = text.encode("utf-8", "replace") if isinstance(text, str) else text
    n = len(raw)
    if n <= cap:
        return raw.decode("utf-8", "ignore"), n
    return raw[:cap].decode("utf-8", "ignore") + "\n...[truncated]", n


def parse_findings_json(payload: dict) -> list[Finding]:
    """Map the skill's findings.json (docs/OUTPUT_SCHEMA.md) into Finding objects."""
    out: list[Finding] = []
    for f in payload.get("findings", []):
        try:
            out.append(
                Finding(severity=f.get("severity", "low"),
                        category=f.get("category", "unknown"),
                        file=f.get("file", ""),
                        line=f.get("line"),
                        title=f.get("title", ""),
                        evidence=f.get("evidence", ""),
                        recommendation=f.get("recommendation", ""),
                        confidence=float(f.get("confidence", 0.5)),
                        source=f.get("source", "static"),
                        rule_id=f.get("rule_id")))
        except Exception:  # noqa: BLE001 - a malformed row is skipped, not fatal
            continue
    return out


def run_local(
    scan_dir: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    max_bytes: int = MAX_OUTPUT_BYTES,
    policy: "ReviewPolicy | None" = None,
) -> tuple[list[Finding], SandboxRunResult]:
    """Run the scanners in a subprocess against ``scan_dir``; never raises.

    If ``policy`` denies the action (or requires human review), the subprocess is NOT launched and a
    blocked ``SandboxRunResult`` is returned instead (requirement 7).
    """
    out_file = Path(tempfile.mkdtemp(prefix="cr_out_")) / "findings.json"
    cmd = [sys.executable, str(_SKILL_SCRIPT), "--target", scan_dir, "--out", str(out_file)]

    blocked = _gate(policy, cmd, scan_dir, timeout)
    if blocked is not None:
        return [], blocked

    started = time.monotonic()
    timed_out = False
    exit_code = 0
    stdout: str | bytes | None = ""
    stderr: str | bytes | None = ""
    try:
        from .policy import sandbox_env
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False, env=sandbox_env())
        exit_code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out, exit_code = True, -1
        stdout, stderr = exc.stdout, exc.stderr
    duration = time.monotonic() - started

    findings: list[Finding] = []
    if not timed_out and out_file.exists():
        try:
            findings = parse_findings_json(json.loads(out_file.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 - unreadable output degrades the source, not the task
            findings = []

    _, out_bytes = _truncate(stdout, max_bytes)
    _, err_bytes = _truncate(stderr, max_bytes)
    result = SandboxRunResult(script="run_checks.py",
                              exit_code=exit_code,
                              duration_sec=round(duration, 3),
                              timed_out=timed_out,
                              stdout_bytes=out_bytes,
                              stderr_bytes=err_bytes)
    return findings, result


async def run_container(
    scan_dir: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    max_bytes: int = MAX_OUTPUT_BYTES,
    memory_mb: int = 512,
    image: str = "cr-scanners:latest",
) -> tuple[list[Finding], SandboxRunResult]:
    """Production isolation: run the scanners inside a container workspace. Requires Docker + image.

    Stages the changed files into the workspace, runs ``run_checks.py`` with a timeout and resource
    limits, collects ``findings.json``, and truncates captured output to the byte cap.
    """
    from trpc_agent_sdk.code_executors import (WorkspaceOutputSpec, WorkspacePutFileInfo, WorkspaceResourceLimits,
                                               WorkspaceRunProgramSpec, create_container_workspace_runtime)
    from trpc_agent_sdk.code_executors.container import ContainerConfig

    runtime = create_container_workspace_runtime(container_config=ContainerConfig(image=image))
    manager, fs, runner = runtime.manager(None), runtime.fs(None), runtime.runner(None)
    exec_id = "cr-" + Path(scan_dir).name

    started = time.monotonic()
    ws = await manager.create_workspace(exec_id)
    try:
        files = [
            WorkspacePutFileInfo(path=str(p.relative_to(scan_dir)), content=p.read_bytes())
            for p in Path(scan_dir).rglob("*") if p.is_file()
        ]
        await fs.put_files(ws, files)
        from .policy import sandbox_env
        run = await runner.run_program(
            ws,
            WorkspaceRunProgramSpec(cmd="python",
                                    args=["/opt/skill/run_checks.py", "--target", ".", "--out", "findings.json"],
                                    env=sandbox_env(),
                                    timeout=timeout,
                                    limits=WorkspaceResourceLimits(memory_mb=memory_mb)))
        collected = await fs.collect_outputs(ws, WorkspaceOutputSpec(globs=["findings.json"]))
        findings: list[Finding] = []
        for cf in getattr(collected, "files", collected) or []:
            try:
                findings = parse_findings_json(json.loads(cf.content.decode("utf-8")))
            except Exception:  # noqa: BLE001
                continue
        _, out_bytes = _truncate(run.stdout, max_bytes)
        _, err_bytes = _truncate(run.stderr, max_bytes)
        result = SandboxRunResult(script="run_checks.py",
                                  exit_code=run.exit_code,
                                  duration_sec=round(time.monotonic() - started, 3),
                                  timed_out=run.timed_out,
                                  stdout_bytes=out_bytes,
                                  stderr_bytes=err_bytes)
        return findings, result
    finally:
        await manager.cleanup(exec_id)
