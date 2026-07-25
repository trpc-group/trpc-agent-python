"""Native SkillToolSet factory and deterministic code-review FunctionTools."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REVIEW_SKILL_TOOL_NAMES = ["skill_load", "skill_select_docs", "skill_list_docs"]
_ANALYZE_PR_COMMAND = "python scripts/pr-analyzer.py --diff-file ../../work/inputs/input.diff"


def plan_review_actions(action_ids: list[str], workspace_inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expand code-review Skill action IDs into fixed no-stdin skill_run inputs."""
    actions: list[dict[str, Any]] = []
    for action_id in action_ids:
        if action_id == "analyze_pr":
            actions.append({"skill": "code-review", "command": _ANALYZE_PR_COMMAND, "cwd": "", "stdin": "",
                            "timeout": 30, "inputs": workspace_inputs})
    return actions


@dataclass(frozen=True)
class WorkspaceInputSpec:
    """Import-free equivalent accepted by SDK SkillRunInput on real execution."""
    src: str
    dst: str
    mode: str = "copy"

    def model_dump(self) -> dict[str, str]:
        return {"src": self.src, "dst": self.dst, "mode": self.mode}


def _stage(path: Path, dst: str) -> WorkspaceInputSpec:
    return WorkspaceInputSpec(f"host://{path.resolve().as_posix()}", dst)


def _env_positive_int(name: str, default: int) -> int:
    """Read a bounded audit limit from the environment."""
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _truncate_text(text: Any, limit_bytes: int) -> tuple[str, bool, int]:
    value = str(text or "")
    raw = value.encode("utf-8")
    if len(raw) <= limit_bytes:
        return value, False, len(raw)
    # Keep both ends: command failures frequently place the useful reason last.
    half = max(1, limit_bytes // 2)
    head = raw[:half].decode("utf-8", errors="ignore")
    tail = raw[-half:].decode("utf-8", errors="ignore")
    return f"{head}\n...[truncated {len(raw) - limit_bytes} bytes]...\n{tail}", True, len(raw)


def _audit_summary(value: Any, limit_bytes: int, redact_text: Any) -> dict[str, Any]:
    text = redact_text(json.dumps(value, ensure_ascii=False, default=str, sort_keys=True))
    preview, truncated, original_bytes = _truncate_text(text, limit_bytes)
    return {
        "preview": preview,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "original_bytes": original_bytes,
        "truncated": truncated,
    }


def _compact_skill_runs(runs: list[dict], limit_bytes: int, redact_text: Any) -> list[dict]:
    compact: list[dict] = []
    for run in runs:
        item = dict(run)
        for field in ("stdout", "stderr"):
            value, truncated, original_bytes = _truncate_text(redact_text(item.get(field, "")), limit_bytes)
            item[field] = value
            item[f"{field}_truncated"] = truncated
            item[f"{field}_original_bytes"] = original_bytes
        compact.append(item)
    return compact


def _compact_model_runs(runs: list[dict], limit: int, limit_bytes: int, redact_text: Any) -> list[dict]:
    return [{
        "model": item.get("model", ""),
        "input_summary": _audit_summary(item.get("input", {}), limit_bytes, redact_text),
        "output_summary": _audit_summary(item.get("output", {}), limit_bytes, redact_text),
        "duration_seconds": item.get("duration_seconds", 0),
        "exception": redact_text(item.get("exception", "")),
    } for item in runs[:limit]]


def _review_metrics(findings: list[dict], human: list[dict], skill_runs: list[dict], model_runs: list[dict],
                    decisions: list[dict], total_duration: float) -> dict[str, Any]:
    severity: dict[str, int] = {}
    for finding in findings + human:
        severity[finding["severity"]] = severity.get(finding["severity"], 0) + 1
    statuses: dict[str, int] = {}
    for run in skill_runs:
        statuses[run.get("status", "unknown")] = statuses.get(run.get("status", "unknown"), 0) + 1
    exceptions: dict[str, int] = {}
    for run in model_runs:
        exception = str(run.get("exception", "")).strip()
        if exception:
            exceptions[exception] = exceptions.get(exception, 0) + 1
    return {
        "total_duration_seconds": round(max(0.0, total_duration), 3),
        "sandbox_duration_seconds": round(sum(float(item.get("duration_seconds", 0) or 0) for item in skill_runs), 3),
        "tool_call_count": len(skill_runs),
        "finding_count": len(findings),
        "needs_human_review_count": len(human),
        "sandbox_run_count": len(skill_runs),
        "blocked_count": sum(item.get("decision") != "allow" for item in decisions),
        "severity_distribution": severity,
        "exception_distribution": exceptions,
        "skill_run_status_distribution": statuses,
    }


def _markdown_report(task_id: str, findings: list[dict], human: list[dict], metrics: dict,
                     skill_runs: list[dict], decisions: list[dict], model_runs: list[dict]) -> str:
    lines = [f"# Code Review: {task_id}", "", "## Findings"]
    lines += [f"- [{item['severity']}] `{item['file']}:{item['line']}` {item['title']}" for item in findings + human] or ["- No findings."]
    lines += ["", "## 监控指标"]
    lines += [f"- {key}: {value}" for key, value in metrics.items()]
    lines += ["", "## 沙箱执行摘要"]
    lines += [f"- [{item.get('status')}] `{item.get('command')}` (exit={item.get('exit_code')}, {item.get('duration_seconds', 0)}s)" for item in skill_runs] or ["- 未执行沙箱命令。"]
    lines += ["", "## Filter 拦截摘要"]
    lines += [f"- [{item.get('decision')}] {item.get('reason')}" for item in decisions] or ["- 无拦截。"]
    lines += ["", "## 模型调用摘要"]
    lines += [f"- `{item.get('model')}` ({item.get('duration_seconds', 0)}s, input={item['input_summary']['original_bytes']} bytes, output={item['output_summary']['original_bytes']} bytes)" for item in model_runs] or ["- 未调用模型。"]
    return "\n".join(lines) + "\n"


def parse_review_input(*, diff: str = "", diff_file: str = "", repo_path: str = "", files: list[str] = [],
                       task_id: str = "", output_dir: str = "", staging_dir: str = "",
                       tool_context: Any = None) -> dict[str, Any]:
    """Parse and redact input; declare files copied to ``$WORK_DIR/inputs``."""
    if not diff and not diff_file and not repo_path and not files:
        raise ValueError("one of diff, diff_file, repo_path, or files is required")
    patch = Path(diff_file).resolve() if diff_file else None
    root = Path(repo_path).resolve() if repo_path else None
    selected = [Path(item).resolve() for item in files or []]
    if patch:
        diff = patch.read_text(encoding="utf-8")
    elif root:
        completed = subprocess.run(["git", "-C", str(root), "diff", "--no-ext-diff", "--"], capture_output=True, text=True, check=False)
        if completed.returncode:
            raise ValueError(f"could not read repository diff: {completed.stderr.strip()}")
        diff = completed.stdout
    elif not diff:
        sections = []
        for source in selected:
            lines = source.read_text(encoding="utf-8").splitlines()
            sections += [f"+++ b/{source.name}", f"@@ -0,0 +1,{len(lines)} @@", *[f"+{line}" for line in lines]]
        diff = "\n".join(sections) + "\n"

    from .parser import parse_unified_diff_with_hunks
    from .redactor import redact
    parsed = parse_unified_diff_with_hunks(diff)
    redacted_diff = redact(diff)
    # ContainerWorkspaceRuntime copies a host input into ``dirname(dst)``.
    # Stage the parent directory (not the file, whose tar member is ``.``),
    # while retaining the filename in dst so the archive lands at
    # ``work/inputs/input.diff``.
    staged = [_stage(patch.parent, "work/inputs/input.diff")] if patch else []
    if root and staging_dir:
        generated_diff = Path(staging_dir) / "input.diff"
        generated_diff.parent.mkdir(parents=True, exist_ok=True)
        generated_diff.write_text(redacted_diff, encoding="utf-8")
        staged.append(_stage(generated_diff.parent, "work/inputs/input.diff"))
    sources = {item for item in selected if item.is_file()}
    if root:
        for line in parsed.changed_lines:
            candidate = (root / line.file).resolve()
            if candidate.is_file():
                sources.add(candidate)
    for source in sorted(sources):
        relative = source.relative_to(root).as_posix() if root and source.is_relative_to(root) else source.name
        staged.append(_stage(source, f"work/inputs/{relative}"))
    unique = {item.dst: item for item in staged}
    changed = [{"file": item.file, "line": item.line, "content": redact(item.content)} for item in parsed.changed_lines]
    result = {
        "diff": redacted_diff, "changed_files": sorted({item["file"] for item in changed if item["file"]}),
        "changed_lines": changed,
        "hunks": [{"file": item.file, "header": item.header, "context": [redact(text) for text in item.context]} for item in parsed.hunks],
        "workspace_inputs": [item.model_dump() for item in unique.values()],
    }
    if tool_context is not None:
        state = tool_context.agent_context.metadata
        # A model often calls this tool with inline diff text. Such a call has
        # no host file to stage, so retain the input mappings pre-seeded from
        # the original CLI payload instead of accidentally discarding them.
        if not result["workspace_inputs"] and state.get("code_review_workspace_inputs"):
            result["workspace_inputs"] = state["code_review_workspace_inputs"]
        state["code_review_workspace_inputs"] = result["workspace_inputs"]
        state["code_review_changed_lines"] = changed
    return result


def save_review_report(*, task_id: str, findings: list[dict], evidence: dict, output_dir: str = "", tool_context: Any = None) -> dict[str, Any]:
    """Validate evidence, apply deterministic rules, deduplicate, redact and persist."""
    from .parser import ChangedLine
    from .redactor import redact
    from .rules import scan
    from .storage import ReviewStorage

    review_started = time.monotonic()
    if tool_context is not None:
        state = tool_context.agent_context.metadata
        review_started = state.get("code_review_started_at", review_started)
        evidence = {
            **evidence,
            "changed_lines": state.get("code_review_changed_lines", evidence.get("changed_lines", [])),
            "skill_runs": state.get("code_review_skill_runs", evidence.get("skill_runs", [])),
            "model_runs": state.get("code_review_model_runs", evidence.get("model_runs", [])),
            "filter_decisions": state.get("code_review_filter_decisions", evidence.get("filter_decisions", [])),
        }
    changed = {(item.get("file"), item.get("line")) for item in evidence.get("changed_lines", [])}
    evidence_text = "\n".join(str(item.get("content", "")) for item in evidence.get("changed_lines", []))
    deterministic = [item.as_dict() for item in scan([ChangedLine(str(item.get("file", "")), int(item.get("line", 0)), str(item.get("content", ""))) for item in evidence.get("changed_lines", [])])]
    accepted, human, seen = [], [], set()
    for raw in [*deterministic, *findings]:
        try:
            item = {key: raw[key] for key in ("severity", "category", "file", "line", "title", "evidence", "recommendation", "confidence", "source")}
            item["line"], item["confidence"] = int(item["line"]), float(item["confidence"])
        except (KeyError, TypeError, ValueError):
            continue
        item["evidence"] = redact(str(item["evidence"]))
        key = (item["category"], item["file"], item["line"])
        if key in seen or (item["file"], item["line"]) not in changed or (item["evidence"] and item["evidence"] not in redact(evidence_text)):
            continue
        seen.add(key)
        (human if item["confidence"] < 0.7 else accepted).append(item)
    root, task_root = Path(output_dir or Path(__file__).parents[1] / "review-output"), None
    task_root = root / task_id
    task_root.mkdir(parents=True, exist_ok=True)
    tool_limit = _env_positive_int("CODE_REVIEW_TOOL_OUTPUT_MAX_KIB", 8) * 1024
    model_limit = _env_positive_int("CODE_REVIEW_MODEL_AUDIT_MAX_KIB", 16) * 1024
    model_count = _env_positive_int("CODE_REVIEW_MODEL_RUN_MAX_COUNT", 20)
    raw_model_runs = evidence.get("model_runs", [])
    skill_runs = _compact_skill_runs(evidence.get("skill_runs", []), tool_limit, redact)
    model_runs = _compact_model_runs(raw_model_runs, model_count, model_limit, redact)
    decisions = evidence.get("filter_decisions", [])
    metrics = _review_metrics(accepted, human, skill_runs, raw_model_runs, decisions, time.monotonic() - review_started)
    report = {"task_id": task_id, "status": "completed", "findings": accepted, "needs_human_review": human,
              "skill_runs": skill_runs, "model_runs": model_runs, "filter_decisions": decisions,
              "metrics": metrics}
    json_path, markdown_path = task_root / "review_report.json", task_root / "review_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown_report(task_id, accepted, human, metrics, skill_runs, decisions, model_runs), encoding="utf-8")
    ReviewStorage(root / "reviews.sqlite").save_native(task_id, report, hashlib.sha256(redact(evidence_text).encode()).hexdigest())
    return {**report, "json_path": str(json_path), "markdown_path": str(markdown_path)}


def _review_action_tool(skill_tools: Any) -> Any:
    """Create the only model-visible execution tool; it delegates to native skill_run."""
    async def run_selected_review_actions(*, action_ids: list[str], tool_context: Any = None) -> dict[str, Any]:
        if tool_context is None:
            raise ValueError("tool_context is required")
        inputs = tool_context.agent_context.metadata.get("code_review_workspace_inputs", [])
        actions = plan_review_actions(action_ids, inputs)
        if not actions:
            return {"runs": [], "skipped": action_ids}
        runs = []
        for action in actions:
            runs.append(await skill_tools._run_tool.run_async(tool_context=tool_context, args=action))
        return {"runs": runs, "skipped": [item for item in action_ids if item != "analyze_pr"]}

    return run_selected_review_actions


def create_review_tools(runtime: str = "docker") -> tuple[Any, list[Any], Any]:
    """Build SDK SkillToolSet plus exactly parse/save FunctionTools."""
    from trpc_agent_sdk.skills import SkillToolSet, create_default_skill_repository
    from trpc_agent_sdk.skills.tools import CopySkillStager
    from trpc_agent_sdk.tools import FunctionTool
    from .filter import CodeReviewSkillRunFilter
    if runtime == "local":
        from trpc_agent_sdk.code_executors import create_local_workspace_runtime
        workspace = create_local_workspace_runtime()
    elif runtime == "docker":
        from trpc_agent_sdk.code_executors import create_container_workspace_runtime
        from trpc_agent_sdk.code_executors.container import ContainerConfig
        example_root = Path(__file__).parents[1]
        workspace = create_container_workspace_runtime(container_config=ContainerConfig(
            image=os.getenv("CODE_REVIEW_DOCKER_IMAGE", "trpc-code-review:latest"),
            docker_path=os.getenv("CODE_REVIEW_DOCKER_BUILD_CONTEXT", str(example_root)),
        ))
    else:
        raise RuntimeError("Cube/E2B runtime must be constructed asynchronously by the SDK deployment entry point")
    repository = create_default_skill_repository(str(Path(__file__).parents[1] / "skills"), workspace_runtime=workspace)
    skill_tools = SkillToolSet(repository=repository, require_skill_loaded=True, allowed_cmds=["python", "ruff", "pytest"], filters=[CodeReviewSkillRunFilter()], force_save_artifacts=True, skill_stager=CopySkillStager(), tool_filter=REVIEW_SKILL_TOOL_NAMES, is_include_all_tools=False)
    return skill_tools, [FunctionTool(_review_action_tool(skill_tools)), FunctionTool(save_review_report)], repository


async def create_review_tools_async(runtime: str = "docker") -> tuple[Any, list[Any], Any]:
    """Create the Cube/E2B-backed ToolSet when the runtime needs async startup."""
    if runtime not in {"cube", "e2b"}:
        return create_review_tools(runtime)
    from trpc_agent_sdk.code_executors.cube import CubeClientConfig, CubeWorkspaceRuntimeConfig
    from trpc_agent_sdk.code_executors.cube import create_cube_sandbox_client, create_cube_workspace_runtime
    from trpc_agent_sdk.skills import SkillToolSet, create_default_skill_repository
    from trpc_agent_sdk.skills.tools import CopySkillStager
    from trpc_agent_sdk.tools import FunctionTool
    from .filter import CodeReviewSkillRunFilter
    config = CubeClientConfig(execute_timeout=30.0, idle_timeout=600, auto_recover=True)
    client = await create_cube_sandbox_client(config)
    workspace = create_cube_workspace_runtime(sandbox_client=client, execute_timeout=config.execute_timeout,
                                               workspace_cfg=CubeWorkspaceRuntimeConfig())
    repository = create_default_skill_repository(str(Path(__file__).parents[1] / "skills"), workspace_runtime=workspace)
    skill_tools = SkillToolSet(repository=repository, require_skill_loaded=True, allowed_cmds=["python", "ruff", "pytest"], filters=[CodeReviewSkillRunFilter()], force_save_artifacts=True, skill_stager=CopySkillStager(), tool_filter=REVIEW_SKILL_TOOL_NAMES, is_include_all_tools=False)
    return skill_tools, [FunctionTool(_review_action_tool(skill_tools)), FunctionTool(save_review_report)], repository
