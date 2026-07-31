# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Development runner: invoke the code-review Skill's script in a subprocess.

Issue #92 permits a local runtime **as a development fallback only** — production isolation is the
container/Cube workspace the agent drives through ``skill_run``. This module is that fallback, and
it exists so the deterministic acceptance harness (``run_review.py``, ``selftest.py``) can score the
fixture corpus without a model or a Docker daemon.

It is not a second implementation of the review: it launches the **same**
``skills/code-review/scripts/run_checks.py`` the Skill stages into its sandbox. Two ways to *launch*
one script are not duplication; two ways to *decide* findings would be.

The process boundary is real — timeout, output-size cap, and a whitelisted environment — and the
policy gate runs *before* the subprocess is launched (requirement 7), so a denied action never
executes.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .types import SandboxRunResult

if TYPE_CHECKING:
    from .policy import ReviewPolicy

SKILL_SCRIPT = Path(__file__).resolve().parents[3] / "skills" / "code-review" / "scripts" / "run_checks.py"
DEFAULT_TIMEOUT_SEC = 60.0
MAX_OUTPUT_BYTES = 1_048_576  # 1 MiB per stream

_SCRIPT_NAME = "run_checks.py"


def _gate(policy: "ReviewPolicy | None", cmd: list[str], scan_dir: str, timeout: float) -> SandboxRunResult | None:
    """Return a blocked result if the policy refuses the action, else None (allowed to run)."""
    if policy is None:
        return None
    decision = policy.evaluate(command=" ".join(cmd), touched_paths=[scan_dir], budget_sec=timeout)
    if decision.allowed:
        return None
    return SandboxRunResult(script=_SCRIPT_NAME,
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


def run_checks_subprocess(
    scan_dir: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    max_bytes: int = MAX_OUTPUT_BYTES,
    policy: "ReviewPolicy | None" = None,
) -> tuple[dict[str, Any], SandboxRunResult]:
    """Run the skill's scanner script against ``scan_dir``; never raises.

    Returns the findings envelope (``docs/OUTPUT_SCHEMA.md``) and the execution record. The envelope
    is ``{}`` when the run was blocked, timed out, or produced nothing readable — a degraded source,
    not a failed task.

    The script locates its own scan root from the ``.changes.diff`` sidecar that
    ``engine.materialize_diff`` leaves next to the changed files, so no ``--diff`` flag is needed.
    """
    out_file = Path(tempfile.mkdtemp(prefix="cr_out_")) / "findings.json"
    cmd = [sys.executable, str(SKILL_SCRIPT), "--target", scan_dir, "--out", str(out_file)]

    blocked = _gate(policy, cmd, scan_dir, timeout)
    if blocked is not None:
        return {}, blocked

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

    payload: dict[str, Any] = {}
    if not timed_out and out_file.exists():
        try:
            loaded = json.loads(out_file.read_text(encoding="utf-8"))
            payload = loaded if isinstance(loaded, dict) else {}
        except Exception:  # noqa: BLE001 - unreadable output degrades the source, not the task
            payload = {}

    _, out_bytes = _truncate(stdout, max_bytes)
    _, err_bytes = _truncate(stderr, max_bytes)
    return payload, SandboxRunResult(script=_SCRIPT_NAME,
                                     exit_code=exit_code,
                                     duration_sec=round(duration, 3),
                                     timed_out=timed_out,
                                     stdout_bytes=out_bytes,
                                     stderr_bytes=err_bytes)
