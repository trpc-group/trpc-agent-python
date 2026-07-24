#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Code Review Agent — CLI entry & full orchestration (Phase 5).

Phase 5 scope: a single ``agent.py`` that orchestrates the whole pipeline —
parse a unified diff into a structured ``ChangeSet`` → load the
``code-review`` Skill (rules + scripts + sandbox config) → run the
``FilterGovernance`` checks (deny/review skip the sandbox) → execute the
checks (``FakeRunner`` in dry-run, an SDK-backed sandbox otherwise —
honoring the Skill's ``default_runtime``/``fallback`` contract, e.g. a
``container`` backend with transparent ``local`` fallback) → dedupe &
triage → persist to the Phase-0
:class:`ReviewStore` → render the eight-section ``review_report.json`` /
``.md``.

Inputs supported
----------------
* ``--diff-file PATH``   parse a unified-diff file.
* ``--repo-path PATH``   run ``git diff HEAD`` inside the repo (staged + unstaged).
* ``--fixture NAME``     use a built-in sample diff (``clean`` / ``security``).

The Skill is resolved relative to this file by default
(``skills/code-review``) and can be overridden with ``--skill-dir``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# --- import wiring --------------------------------------------------------- #
# This file lives at examples/skills_code_review_agent/agent/agent.py.
_HERE = Path(__file__).resolve().parent  # .../agent
_EXAMPLE_ROOT = _HERE.parent  # .../skills_code_review_agent
# Make the `agent` package importable when run as a standalone script
# (e.g. `python agent/agent.py`) — normally run_agent.py / tests already do this.
if str(_EXAMPLE_ROOT) not in sys.path:
    sys.path.insert(0, str(_EXAMPLE_ROOT))
from agent.db import SQLiteStore  # noqa: E402
from agent.db import ReviewStore  # noqa: E402

# Make the skill scripts importable (parse_diff / run_checks / mask_secrets / dedupe).
_SKILL_DIR_DEFAULT = _EXAMPLE_ROOT / "skills" / "code-review"
_SCRIPTS_DIR = _SKILL_DIR_DEFAULT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from parse_diff import ChangeSet  # noqa: E402
from parse_diff import ChangedFile  # noqa: E402
from parse_diff import parse_diff  # noqa: E402


# --------------------------------------------------------------------------- #
# Built-in fixture diffs (for demo / dry-run without a real repo)
# --------------------------------------------------------------------------- #
FIXTURES: dict[str, str] = {
    "clean": """\
diff --git a/clean.py b/clean.py
--- a/clean.py
+++ b/clean.py
@@ -1,3 +1,5 @@
 def add(a, b):
-    return a + b
+    return a + b
+
+
 def mul(a, b):
""",
    "security": """\
diff --git a/app/db.py b/app/db.py
--- a/app/db.py
+++ b/app/db.py
@@ -20,3 +20,7 @@
 def get_user(conn, uid):
-    cur = conn.execute("SELECT * FROM users WHERE id=" + str(uid))
-    return cur.fetchone()
+    # parameterized query — safe
+    cur = conn.execute("SELECT * FROM users WHERE id=?", (uid,))
+    return cur.fetchone()
diff --git a/app/secret.py b/app/secret.py
--- /dev/null
+++ b/app/secret.py
@@ -0,0 +1,2 @@
+API_KEY = "sk-1234567890abcdef1234567890abcdef"
+TOKEN = "ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890"
""",
    "llm_probe": """\
diff --git a/svc/client.py b/svc/client.py
--- /dev/null
+++ b/svc/client.py
@@ -0,0 +1,7 @@
+import requests
+
+API_KEY = "sk-abcdef1234567890abcdef1234567890ab"
+
+def call(endpoint, payload):
+    # TLS 校验关闭：有时内网有意为之，需人工确认
+    return requests.post(endpoint, json=payload, verify=False)
""",
}


# --------------------------------------------------------------------------- #
# SKILL.md frontmatter parsing
# --------------------------------------------------------------------------- #
def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Return (frontmatter_dict, body) from a SKILL.md text.

    Frontmatter is the YAML between leading ``---`` fences. If no fences
    are present, returns ``({}, text)``.
    """
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm_text = parts[1].strip("\n")
    body = parts[2].lstrip("\n")
    return _parse_yaml(fm_text), body


def _parse_yaml(text: str) -> dict:
    """Minimal YAML parser for SKILL.md frontmatter.

    Prefers PyYAML when available; falls back to a small indentation-aware
    parser that handles the subset SKILL.md uses: scalars, block & flow
    lists, nested dicts, and ``>-`` / ``>`` / ``|`` block scalars.
    """
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    except ImportError:
        return _parse_yaml_fallback(text)


def _parse_yaml_fallback(text: str) -> dict:
    """Indentation-aware YAML subset parser (no external deps)."""
    lines = text.splitlines()
    root: dict = {}
    # Stack of (indent, container) where container is dict or list.
    stack: list[tuple[int, dict | list]] = [(0, root)]
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        # Pop stack to the current indent level.
        while stack and stack[-1][0] > indent:
            stack.pop()
        if stack and stack[-1][0] < indent:
            # The previous line opened a block; its container is already on
            # top via the list/dict push below.
            pass
        if stripped.startswith("- "):
            # list item
            item_val = _coerce(stripped[2:].strip())
            container = stack[-1][1] if stack else root
            if isinstance(container, list):
                container.append(item_val)
            i += 1
            continue
        # key: value
        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            container = stack[-1][1] if stack else root
            if not isinstance(container, dict):
                i += 1
                continue
            if val == "":
                # Block follows: peek next non-empty line indent.
                if i + 1 < len(lines):
                    nxt = lines[i + 1]
                    nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                    if nxt.strip().startswith("- "):
                        new_list: list = []
                        container[key] = new_list
                        stack.append((nxt_indent, new_list))
                    elif nxt_indent > indent:
                        new_dict: dict = {}
                        container[key] = new_dict
                        stack.append((nxt_indent, new_dict))
                else:
                    container[key] = None
            elif val in (">-", ">", "|"):
                # Block scalar — collect deeper-indented lines.
                collected: list[str] = []
                j = i + 1
                while j < len(lines):
                    nxt = lines[j]
                    if nxt.strip() == "":
                        collected.append("")
                        j += 1
                        continue
                    nxt_indent = len(nxt) - len(nxt.lstrip(" "))
                    if nxt_indent <= indent:
                        break
                    collected.append(nxt.strip())
                    j += 1
                container[key] = " ".join(x for x in collected if x)
                i = j
                continue
            else:
                container[key] = _coerce(val)
        i += 1
    return root


def _coerce(val: str):
    """Coerce a YAML scalar string to int/float/bool/None/list/str."""
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        return [_coerce(x.strip()) for x in inner.split(",")]
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    if val.startswith("'") and val.endswith("'"):
        return val[1:-1]
    if val.lower() in ("true", "false"):
        return val.lower() == "true"
    if val.lower() in ("null", "~", ""):
        return None
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


# --------------------------------------------------------------------------- #
# skill_load
# --------------------------------------------------------------------------- #
def skill_load(skill_dir: str | Path) -> dict:
    """Load a code-review Skill via the SDK ``FsSkillRepository``.

    The SDK repository parses the SKILL.md frontmatter (name/description) and
    loads the skill body + resources (rules/*.md). Two pieces are NOT exposed
    by the SDK ``Skill`` object and are filled here:
    * ``sandbox_config`` — parsed from the SKILL.md frontmatter (sandbox: block)
    * ``scripts`` — scanned from ``scripts/*.py`` on disk

    Returns::

        {
            "name": "code-review",
            "skill_dir": "/abs/path/to/skills/code-review",
            "rules": ["rules/security.md", ...],
            "scripts": ["scripts/parse_diff.py", ...],
            "sandbox_config": {default_runtime, fallback, timeout_s, ...},
        }
    """
    # Lazy import: callers that don't invoke skill_load (e.g. P2 rule-engine
    # tests importing agent for FIXTURES) aren't forced to load the SDK.
    from trpc_agent_sdk.skills import FsSkillRepository

    skill_dir = Path(skill_dir)
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"SKILL.md not found: {skill_md}")

    # SDK: discover + load the skill (frontmatter name/description + body + resources).
    repo = FsSkillRepository(str(skill_dir.parent))
    frontmatter, _ = _split_frontmatter(skill_md.read_text(encoding="utf-8"))
    name = frontmatter.get("name", skill_dir.name)
    skill = repo.get(name)
    base = Path(skill.base_dir)

    # rules: prefer SDK resources (rules/*.md with content), fall back to disk scan.
    def _res_path(r):
        return getattr(r, "path", None) or (r.get("path") if isinstance(r, dict) else None)

    rules = sorted({
        p for r in skill.resources
        if (p := _res_path(r)) and p.startswith("rules/")
    }) or _rel_list(base / "rules", "*.md", base)

    # scripts: scan disk (SDK resources don't include .py scripts).
    scripts = _rel_list(base / "scripts", "*.py", base)

    return {
        "name": name,
        "skill_dir": str(base),
        "rules": rules,
        "scripts": scripts,
        "sandbox_config": frontmatter.get("sandbox", {}),
    }


def _rel_list(directory: Path, pattern: str, base: Path) -> list[str]:
    """Sorted list of paths under ``directory`` matching ``pattern``, relative to ``base``."""
    if not directory.exists():
        return []
    out = []
    for p in sorted(directory.glob(pattern)):
        rel = p.relative_to(base).as_posix()
        out.append(rel)
    return out


# --------------------------------------------------------------------------- #
# Input collection
# --------------------------------------------------------------------------- #
def load_diff(
    diff_file: str | None = None,
    repo_path: str | None = None,
    fixture: str | None = None,
) -> tuple[str, str, str]:
    """Collect a diff text from one of three sources.

    Returns ``(diff_text, input_type, input_ref)`` where ``input_type`` is
    ``diff`` / ``repo`` / ``fixture`` and ``input_ref`` is a human-readable
    reference (file path, repo path, or fixture name).
    """
    if diff_file:
        path = Path(diff_file)
        if not path.exists():
            raise FileNotFoundError(f"diff file not found: {path}")
        return (path.read_text(encoding="utf-8"), "diff", str(path))
    if repo_path:
        return (_git_diff_head(repo_path), "repo", str(repo_path))
    if fixture:
        if fixture not in FIXTURES:
            raise KeyError(
                f"unknown fixture '{fixture}', available: {list(FIXTURES)}"
            )
        return (FIXTURES[fixture], "fixture", fixture)
    raise ValueError("no input source provided (use --diff-file/--repo-path/--fixture)")


def _git_diff_head(repo_path: str) -> str:
    """Run ``git diff HEAD`` in ``repo_path`` (staged + unstaged changes)."""
    try:
        proc = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("git executable not found on PATH") from exc
    if proc.returncode != 0 and proc.stderr:
        # Non-fatal: empty diff on a fresh repo is fine; surface real errors.
        if "not a git repository" in proc.stderr.lower():
            raise RuntimeError(f"not a git repository: {repo_path}")
    return proc.stdout


# --------------------------------------------------------------------------- #
# Persistence helpers
# --------------------------------------------------------------------------- #
def _file_sha256(file: ChangedFile, path: str) -> str:
    """Stable SHA-256 of a changed file's diff content for dedup/identity."""
    parts = [path]
    for hunk in file.hunks:  # type: ignore[attr-defined]
        for ln in hunk.lines:  # type: ignore[attr-defined]
            parts.append(f"{ln.type}|{ln.content}")  # type: ignore[attr-defined]
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    return digest


async def persist_changeset(
    store: ReviewStore,
    task_id: str,
    cs: ChangeSet,
) -> int:
    """Write each ``ChangedFile`` as an ``input_diff`` row. Returns row count."""
    count = 0
    for f in cs.files:
        summary = f"{f.hunk_count} hunks, {f.added_lines}+, {f.deleted_lines}-"
        await store.add_input_diff(
            task_id=task_id,
            file_path=f.path,
            sha256=_file_sha256(f, f.path),
            hunk_count=f.hunk_count,
            line_count=f.line_count,
            summary=summary,
        )
        count += 1
    return count


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Code Review Agent — parse diff, load skill, persist inputs.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--diff-file", help="path to a unified diff file")
    src.add_argument("--repo-path", help="repo root to run `git diff HEAD` in")
    src.add_argument(
        "--fixture",
        choices=sorted(FIXTURES.keys()),
        help="use a built-in sample diff",
    )
    p.add_argument(
        "--skill-dir",
        default=str(_SKILL_DIR_DEFAULT),
        help="code-review skill directory (default: skills/code-review)",
    )
    p.add_argument(
        "--db-path",
        default="cr_agent.db",
        help="SQLite database path (default: cr_agent.db)",
    )
    p.add_argument(
        "--mode",
        choices=["dry-run", "real"],
        default="dry-run",
        help="review mode recorded on the task (default: dry-run)",
    )
    p.add_argument(
        "--print-changeset",
        action="store_true",
        help="also print the parsed ChangeSet as JSON",
    )
    p.add_argument(
        "--telemetry",
        action="store_true",
        help="enable OTel tracing (spans printed to stdout via ConsoleSpanExporter)",
    )
    p.add_argument(
        "--output-dir",
        default=".",
        help="directory for review_report.json + review_report.md (default: .)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="shortcut for --mode dry-run (no model API, full pipeline)",
    )
    p.add_argument(
        "--enable-llm",
        action="store_true",
        help="enable real-LLM second-opinion triage (also via LLM_ENABLED in .env)",
    )
    p.add_argument(
        "--require-sandbox",
        action="store_true",
        help="production: demand the Skill's default_runtime (e.g. container) "
             "and refuse to fall back to local — enforce an isolated sandbox",
    )
    p.add_argument(
        "--llm-env",
        default=None,
        help="path to an explicit .env file for LLM config (default: project-root/.env)",
    )
    return p


class FakeRunner:
    """dry-run sandbox substitute: imports run_checks directly, no model API.

    Used when ``mode == "dry-run"`` (or as a degradation fallback when the real
    sandbox fails). Produces the same ``list[RawFinding]`` as the sandbox path.
    """

    async def run(self, changeset, ruleset):
        from run_checks import run_checks as _run

        return _run(changeset, ruleset)


def _run_checks_rel(skill: dict) -> str | None:
    """Locate the run_checks checker among the skill's declared scripts.

    Returns the skill-relative path (e.g. ``scripts/run_checks.py``) or
    ``None`` when the skill ships no checker. The Filter ``decisions`` dict is
    keyed by these relative paths (see step 2 below), so we match on the
    ``run_checks.py`` tail to find the decision that gates execution.
    """
    for s in skill.get("scripts", []):
        if s.endswith("run_checks.py"):
            return s
    return None


def _script_rel(skill: dict, name_tail: str) -> str | None:
    """Return the skill-relative path of a declared script ending in ``name_tail``.

    Used to look up a script's Filter decision by its declared relative path
    (e.g. ``scripts/parse_diff.py``), independent of where it sits on disk.
    """
    for s in skill.get("scripts", []):
        if s.endswith(name_tail):
            return s
    return None


def _gate(decisions: dict, rel_path: str | None):
    """Return the FilterDecision if it is ``deny``/``needs_human_review``, else None.

    A non-allow verdict on a Skill script means that phase MUST NOT execute the
    script. Callers should stop the phase early (emit an empty/blocked result)
    so the denied script never enters the execution chain — this is the unified
    gate that protects *every* declared script (parse_diff / run_checks /
    dedupe / mask_secrets), not just run_checks.py.
    """
    if not rel_path:
        return None
    d = decisions.get(rel_path)
    if d is not None and getattr(d, "verdict", "allow") != "allow":
        return d
    return None


def build_report(
    task_id: str,
    dedupe_result,
    sandbox_runs: list[dict],
    filter_blocks: list[dict],
    monitor: dict,
    output_dir: str,
) -> tuple[str, str]:
    """Render the eight-section ``review_report.json`` + ``review_report.md``.

    Returns ``(json_path, md_path)``. Both files share the same data source.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "task_id": task_id,
        "1_findings": [f.__dict__ for f in dedupe_result.findings],
        "2_severity_stats": dedupe_result.severity_counts(),
        "3_needs_human_review": [f.__dict__ for f in dedupe_result.needs_human_review],
        "4_filter_blocks": filter_blocks,
        "5_monitor": monitor,
        "6_sandbox_runs": sandbox_runs,
        "7_recommendations": [
            {"file": f.file, "line": f.line, "recommendation": f.recommendation}
            for f in dedupe_result.findings
        ],
        "8_warnings": [f.__dict__ for f in dedupe_result.warnings],
    }
    json_path = out / "review_report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = out / "review_report.md"
    md_path.write_text(_render_report_md(report), encoding="utf-8")
    return str(json_path), str(md_path)


def _render_report_md(report: dict) -> str:
    """Eight-section Markdown — human-readable, same data as the JSON."""
    L: list[str] = [f"# Code Review Report — {report['task_id']}", ""]
    L.append(f"## 1. Findings 摘要（{len(report['1_findings'])} 条）")
    for f in report["1_findings"]:
        L.append(f"- [{f['severity']}] {f['file']}:{f['line']} {f['title']} (conf={f['confidence']})")
    L += ["", "## 2. 严重级别统计"]
    ss = report["2_severity_stats"]
    for sev in ("critical", "high", "medium", "low"):
        L.append(f"- {sev}: {ss.get(sev, 0)}")
    L.append(f"\n## 3. 人工复核项（{len(report['3_needs_human_review'])} 条）")
    for f in report["3_needs_human_review"]:
        L.append(f"- {f['file']}:{f['line']} {f['title']} (conf={f['confidence']})")
    L.append(f"\n## 4. Filter 拦截摘要（{len(report['4_filter_blocks'])} 条）")
    for b in report["4_filter_blocks"]:
        L.append(f"- [{b['verdict']}] {b['reason']}: {b['target']} — {b['detail']}")
    m = report["5_monitor"]
    L += ["", "## 5. 监控指标"]
    for k in ("total_duration_ms", "sandbox_duration_ms", "tool_calls", "blocks",
              "finding_count", "exception_types"):
        L.append(f"- {k}: {m.get(k)}")
    L.append(f"\n## 6. 沙箱执行摘要（{len(report['6_sandbox_runs'])} 次）")
    for s in report["6_sandbox_runs"]:
        L.append(f"- {s.get('runtime','?')}: status={s.get('status','?')} "
                 f"dur={s.get('duration_ms',0)}ms timed_out={s.get('timed_out',0)} "
                 f"masked={s.get('masked_count',0)}")
    L += ["", "## 7. 可执行修复建议"]
    for r in report["7_recommendations"]:
        L.append(f"- {r['file']}:{r['line']}: {r['recommendation']}")
    L.append(f"\n## 8. Warnings（{len(report['8_warnings'])} 条，低置信度，不混入 findings）")
    for f in report["8_warnings"]:
        L.append(f"- [{f['severity']}] {f['file']}:{f['line']} {f['title']} (conf={f['confidence']})")
    return "\n".join(L) + "\n"


async def _async_main(args) -> int:
    """Full pipeline: parse → skill → filter → sandbox → dedupe → persist → report.

    Returns the process exit code: ``0`` on success, ``1`` when the pipeline
    fails (so CI / acceptance harnesses can detect failure instead of a silent
    success). Any failure is logged with ``[agent] ERROR`` and the partial
    task is marked ``failed`` when possible.
    """
    from agent.telemetry import TelemetryRecorder, init_telemetry, trace_stage
    init_telemetry(enabled=args.telemetry)
    recorder = TelemetryRecorder()
    task_id: str | None = None
    exit_code = 0
    if getattr(args, "dry_run", False):
        args.mode = "dry-run"  # --dry-run shortcut → --mode dry-run

    # 1. Load the skill (rules + scripts + sandbox config) via SDK FsSkillRepository.
    async with trace_stage("l2_skill_load", recorder):
        skill = skill_load(args.skill_dir)
    print(f"[agent] skill     : {skill['name']} ({len(skill['rules'])} rules, {len(skill['scripts'])} scripts)")

    # 2. Compute Filter decisions for EVERY declared Skill script (L3) BEFORE
    #    any script executes. The gate is authoritative: a non-allow verdict on
    #    ANY skill script must stop its phase *before* that script runs. This
    #    now covers parse_diff (L1), run_checks (L4), dedupe (L5) and the
    #    mask_secrets helper that dedupe / LLM triage invoke internally.
    #    (Previously only run_checks.py was gated, so parse_diff / dedupe /
    #    mask_secrets could still execute after a deny / needs_human_review.)
    from agent.filters import FilterGovernance

    gov = FilterGovernance()
    decisions: dict[str, object] = {}
    filter_blocks_meta: list[dict] = []
    for script in skill["scripts"]:
        sp = Path(skill["skill_dir"]) / script
        content = sp.read_text(encoding="utf-8") if sp.exists() else ""
        decision = gov.decide(str(sp), content, {})
        decisions[script] = decision
        if decision.verdict != "allow":
            filter_blocks_meta.append(
                {"verdict": decision.verdict, "reason": decision.reason,
                 "target": script, "detail": decision.detail})

    # 3. Collect + parse the diff (L1) — gated on parse_diff's verdict.
    async with trace_stage("l1_parse", recorder):
        diff_text, input_type, input_ref = load_diff(
            diff_file=args.diff_file, repo_path=args.repo_path, fixture=args.fixture)
        parse_rel = _script_rel(skill, "parse_diff.py")
        parse_block = _gate(decisions, parse_rel)
        if parse_block is not None:
            # parse_diff is denied/reviewed → we cannot build a ChangeSet, so
            # the entire review chain stops here. The filter_block above is the
            # signal; nothing downstream (sandbox/dedupe) may run.
            print(f"[agent] filter BLOCKED parse stage: {parse_block.verdict} ({parse_rel})")
            cs = None
        else:
            cs = parse_diff(diff_text)
    print(f"[agent] input     : {input_type} = {input_ref}"
          + (f" | {cs.file_count} file(s)" if cs is not None else " | (parse blocked)"))
    if args.print_changeset and cs is not None:
        print(cs.to_json())

    store = None
    sandbox_runs_meta: list[dict] = []
    raw_findings = []
    try:
        # 4. create_task + persist input_diff + flush recorded filter blocks.
        store = SQLiteStore(args.db_path)
        async with trace_stage("l6_persist", recorder):
            task_id = await store.create_task(input_type, input_ref, args.mode)
            await store.update_task_status(task_id, "running")
            if cs is not None:
                await persist_changeset(store, task_id, cs)
            for b in filter_blocks_meta:
                await store.add_filter_block(
                    task_id, b["reason"], b["target"], b["verdict"], b["detail"])
        print(f"[agent] filter    : {len(filter_blocks_meta)} block(s)")

        # 5–6.5: only when we actually have a ChangeSet (parse_diff was allowed).
        # If parse_diff was filtered, the whole sandbox/dedupe/triage chain is
        # skipped and an empty review is emitted — no Skill script executes.
        if cs is None:
            from cr_models import DedupeResult
            raw_findings = []
            dedupe_result = DedupeResult()
        else:
            from agent.sandbox import (
                SandboxPolicy,
                build_runtime_with_fallback,
                select_runtime,
            )
            policy = SandboxPolicy.from_config(skill["sandbox_config"])

            # 5. Run checks (L4 sandbox).
            # SECURITY GATE — the Filter decision on the checker is authoritative:
            # if it is ``deny``/``needs_human_review``, the script MUST NOT be
            # executed at all. Not in the sandbox, and NOT via the in-process
            # FakeRunner fallback either — running a denied script "locally"
            # outside the sandbox would silently defeat the gate. We emit no raw
            # findings, keep the recorded filter_block as the signal, and mark
            # the (non-) execution as ``blocked`` in the report/DB.
            rc_rel = _run_checks_rel(skill)
            rc_path = str(Path(skill["skill_dir"]) / rc_rel) if rc_rel else ""
            rc_decision = decisions.get(rc_rel) if rc_rel else None
            async with trace_stage("l4_sandbox", recorder):
                try:
                    if rc_decision is not None and rc_decision.verdict != "allow":
                        # Filter blocked the checker → skip execution entirely.
                        # CRITICAL: we do NOT import run_checks here at all, so
                        # neither its module top-level nor load_rules() ever run
                        # for a denied/needs_human_review script.
                        raw_findings = []
                        verdict = rc_decision.verdict  # "deny" | "needs_human_review"
                        sandbox_runs_meta.append(
                            {"runtime": "blocked", "status": verdict, "duration_ms": 0,
                             "exit_code": 0, "output_bytes": 0, "timed_out": 0, "masked_count": 0})
                        # P1-1: the DB must fully record every sandbox-execution
                        # decision, including a blocked one — otherwise the chain
                        # is not testable.
                        await store.add_sandbox_run(
                            task_id, "blocked", rc_rel or "run_checks.py", verdict,
                            0, 0, 0, 0, 0)
                    else:
                        # Allowed → only NOW import the checker module (its
                        # top-level + load_rules run exclusively on the allow
                        # path; a denied checker never reaches this branch).
                        from run_checks import load_rules
                        rules_by_cat = load_rules(skill["skill_dir"], skill["rules"])
                        if args.mode == "dry-run":
                            raw_findings = await FakeRunner().run(cs, {"_rules": rules_by_cat})
                            sandbox_runs_meta.append(
                                {"runtime": "fake", "status": "ok", "duration_ms": 0,
                                 "exit_code": 0, "output_bytes": 0, "timed_out": 0, "masked_count": 0})
                            # P1-1: the DB must fully record every sandbox execution,
                            # including dry-run — otherwise the chain is not testable.
                            await store.add_sandbox_run(
                                task_id, "fake", rc_rel or "run_checks.py", "ok", 0, 0, 0, 0, 0)
                        else:
                            # Real mode: honor the skill's declared sandbox contract.
                            # G1 fix — previously hardcoded to LocalRuntime, ignoring
                            # `default_runtime`/`fallback` from sandbox_config. Now we
                            # try default_runtime (container) and transparently fall
                            # back to `fallback` (local) when it can't be provisioned.
                            sb_cfg = skill["sandbox_config"]
                            default_kind = sb_cfg.get("default_runtime", "local")
                            fallback_kind = sb_cfg.get("fallback", "local")
                            if getattr(args, "require_sandbox", False):
                                # Production: the Skill's declared default_runtime is
                                # the isolated sandbox contract (e.g. container). Do
                                # NOT silently fall back to local — if it can't be
                                # provisioned, fail loudly so the violation is visible.
                                runtime = select_runtime(default_kind, policy)
                                runtime.ensure_available()
                                actual_kind = default_kind
                            else:
                                runtime, actual_kind = build_runtime_with_fallback(
                                    default_kind, fallback_kind, policy)
                            rr = await runtime.run(
                                rc_path, {"changeset": cs.to_dict(), "rules": rules_by_cat}, policy)
                            await store.add_sandbox_run(
                                task_id, actual_kind, rc_rel or "run_checks.py", rr.status, rr.duration_ms,
                                rr.exit_code, rr.output_bytes, int(rr.timed_out), rr.masked_count)
                            sandbox_runs_meta.append(
                                {"runtime": actual_kind, "status": rr.status, "duration_ms": rr.duration_ms,
                                 "exit_code": rr.exit_code, "output_bytes": rr.output_bytes,
                                 "timed_out": int(rr.timed_out), "masked_count": rr.masked_count})
                            if rr.status == "ok" and rr.stdout:
                                from run_checks import RawFinding
                                raw_findings = [RawFinding(**d) for d in json.loads(rr.stdout)]
                            else:
                                raw_findings = await FakeRunner().run(cs, {"_rules": rules_by_cat})
                except Exception as exc:
                    # Sandbox failure degrades to FakeRunner — pipeline never
                    # crashes. (Only reachable when the checker was *allowed*; a
                    # denied checker never reaches this point, so it stays
                    # unexecuted.)
                    recorder.record_exception(type(exc).__name__)
                    raw_findings = await FakeRunner().run(cs, {"_rules": rules_by_cat})
                    sandbox_runs_meta.append(
                        {"runtime": "degraded", "status": "failed", "duration_ms": 0,
                         "exit_code": -1, "output_bytes": 0, "timed_out": 0, "masked_count": 0})
                    await store.add_sandbox_run(
                        task_id, "degraded", rc_rel or "run_checks.py", "failed", 0, -1, 0, 0, 0)
            print(f"[agent] sandbox   : {len(raw_findings)} raw findings")

            # 6. Dedupe (L5) — gated on dedupe AND mask_secrets verdicts.
            # Running dedupe executes mask_secrets on every finding's evidence;
            # if either script is deny/needs_human_review we must NOT run it, so
            # we emit an empty DedupeResult instead (no findings leaked through).
            # Compute the gate FIRST, then import the script module only on allow
            # — otherwise importing ``dedupe`` (a filtered script) would execute
            # its top-level even when the gate is denying it.
            dedupe_block = _gate(decisions, _script_rel(skill, "dedupe.py")) \
                or _gate(decisions, _script_rel(skill, "mask_secrets.py"))
            if dedupe_block is not None:
                from cr_models import DedupeResult
                print(f"[agent] filter BLOCKED dedupe stage: {dedupe_block.verdict} "
                      f"({dedupe_block.target})")
                dedupe_result = DedupeResult()
            else:
                from cr_models import DedupeResult
                from dedupe import dedupe
                dedupe_result = dedupe(raw_findings)
            print(f"[agent] dedupe    : {len(dedupe_result.findings)} findings | "
                  f"{len(dedupe_result.warnings)} warnings | "
                  f"{len(dedupe_result.needs_human_review)} review")

            # 6.5 LLM second-opinion triage (optional, Phase 7).
            # Gated on mask_secrets: the diff is masked before leaving the
            # process, so if mask_secrets is deny/needs_human_review we MUST NOT
            # send the unmasked diff to the model — skip triage entirely.
            async with trace_stage("l5b_llm_triage", recorder):
                from agent.llm import LlmTriage, get_llm_client
                llm_client = get_llm_client(
                    enable=getattr(args, "enable_llm", False),
                    env_path=getattr(args, "llm_env", None))
                ms_block = _gate(decisions, _script_rel(skill, "mask_secrets.py"))
                if llm_client.is_enabled and ms_block is None:
                    dedupe_result = await LlmTriage(llm_client).run(
                        dedupe_result, diff_text)
                    print(f"[agent] llm triage: {llm_client.kind} enabled | "
                          f"{len(dedupe_result.needs_human_review)} still need review")
                elif ms_block is not None:
                    print(f"[agent] llm triage: SKIPPED — mask_secrets blocked "
                          f"({ms_block.verdict})")
                else:
                    print("[agent] llm triage: disabled (LLM_ENABLED=false or no API key)")

        # 7. Persist findings + monitor + report (L6).
        async with trace_stage("l6_persist2", recorder):
            for finding, bucket in dedupe_result.all_with_bucket():
                await store.add_finding(
                    task_id, finding.severity, finding.category, finding.file,
                    finding.line, finding.title, finding.evidence,
                    finding.recommendation, finding.confidence, finding.source, bucket)
            monitor = recorder.to_monitor_summary(
                finding_count=dedupe_result.total,
                sev_counts=dedupe_result.severity_counts(),
                blocks=len(filter_blocks_meta))
            await store.set_monitor_summary(task_id, monitor)
            json_path, md_path = build_report(
                task_id, dedupe_result, sandbox_runs_meta, filter_blocks_meta,
                monitor, args.output_dir)
            summary = (f"{len(dedupe_result.findings)} findings, "
                       f"{len(dedupe_result.warnings)} warnings, "
                       f"{len(dedupe_result.needs_human_review)} review")
            await store.set_report(task_id, json_path, md_path, summary)
            await store.update_task_status(task_id, "done", total_duration_ms=monitor["total_duration_ms"])

        print(f"[agent] task      : {task_id} (done)")
        print(f"[agent] report    : {json_path}")
        print(f"[agent] telemetry : total={monitor['total_duration_ms']}ms exceptions={monitor['exception_types']}")
    except Exception as exc:
        # Top-level fail-safe: record exception, mark task failed, still emit a report.
        # P2-1: signal failure to the caller via a non-zero exit code.
        exit_code = 1
        recorder.record_exception(type(exc).__name__)
        if task_id:
            try:
                await store.update_task_status(task_id, "failed")
                await store.set_monitor_summary(task_id, recorder.to_monitor_summary())
            except Exception:
                pass
        print(f"[agent] ERROR     : {type(exc).__name__}: {exc}")
    finally:
        if store is not None:
            await store.close()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
