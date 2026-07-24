"""tRPC-Agent native wrapper for the code review pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .pipeline import DEFAULT_DB_PATH
from .pipeline import DEFAULT_OUTPUT_DIR
from .pipeline import SKILL_DIR
from .pipeline import run_review

INSTRUCTION = """You are a code review orchestration agent.
Use the code_review tool to inspect diffs, patches, or fixtures.
Return the structured report summary, blocking findings, warnings, Filter decisions, and sandbox status.
"""


async def code_review_tool(
    *,
    diff_file: str | None = None,
    patch_file: str | None = None,
    repo_path: str | None = None,
    file_list: str | None = None,
    fixture: str | None = None,
    output_dir: str | None = None,
    db_path: str | None = None,
    db_url: str | None = None,
    sandbox: str = "container",
    dry_run: bool = False,
    container_image: str = "python:3-slim",
    docker_path: str | None = None,
    docker_base_url: str | None = None,
    cube_template: str | None = None,
    cube_api_url: str | None = None,
    cube_api_key: str | None = None,
    cube_sandbox_id: str | None = None,
    timeout_sec: float = 5.0,
    max_output_bytes: int = 12000,
    filter_timeout_budget_sec: float = 30.0,
    filter_max_output_bytes: int = 20000,
    network_policy: str = "deny",
    test_command: str | None = None,
    custom_rule_script: str | None = None,
    include_network_scanners: bool = False,
    max_diff_bytes: int = 2_000_000,
) -> dict[str, Any]:
    """Run the Skills code review pipeline as a FunctionTool-compatible callable."""
    effective_sandbox = "fake" if dry_run else sandbox
    report = await run_review(
        diff_file=Path(diff_file) if diff_file else None,
        patch_file=Path(patch_file) if patch_file else None,
        repo_path=Path(repo_path) if repo_path else None,
        file_list=Path(file_list) if file_list else None,
        fixture=fixture,
        output_dir=Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR,
        db_path=Path(db_path) if db_path else DEFAULT_DB_PATH,
        db_url=db_url,
        dry_run=dry_run,
        sandbox=effective_sandbox,
        container_image=container_image,
        docker_path=docker_path,
        docker_base_url=docker_base_url,
        cube_template=cube_template,
        cube_api_url=cube_api_url,
        cube_api_key=cube_api_key,
        cube_sandbox_id=cube_sandbox_id,
        timeout_sec=timeout_sec,
        max_output_bytes=max_output_bytes,
        filter_timeout_budget_sec=filter_timeout_budget_sec,
        filter_max_output_bytes=filter_max_output_bytes,
        network_policy=network_policy,
        test_command=test_command,
        custom_rule_script=custom_rule_script,
        include_network_scanners=include_network_scanners,
        max_diff_bytes=max_diff_bytes,
    )
    return {
        "task_id": report.task_id,
        "status": report.status,
        "conclusion": report.conclusion,
        "finding_count": len(report.findings),
        "warning_count": len(report.warnings),
        "needs_human_review_count": len(report.needs_human_review),
        "filter_decision_count": len(report.filter_decisions),
        "sandbox_run_count": len(report.sandbox_runs),
        "outputs": report.output_files,
    }


def create_code_review_skill_tool_set(*, workspace_runtime):
    """Create the framework-native SkillToolSet for skill_load/skill_run."""
    from trpc_agent_sdk.skills import SkillToolSet
    from trpc_agent_sdk.skills import create_default_skill_repository
    from trpc_agent_sdk.skills.tools import LinkSkillStager

    repository = create_default_skill_repository(
        str(SKILL_DIR.parent),
        workspace_runtime=workspace_runtime,
        enable_hot_reload=False,
        use_cached_repository=True,
    )
    return SkillToolSet(repository=repository, skill_stager=LinkSkillStager()), repository


def create_code_review_agent(*, model, skill_workspace_runtime=None):
    """Create an LlmAgent with the review tool and optional skill_load/skill_run tools."""
    from trpc_agent_sdk.agents import LlmAgent
    from trpc_agent_sdk.tools import FunctionTool

    tools = [FunctionTool(code_review_tool)]
    repository = None
    if skill_workspace_runtime is not None:
        skill_tool_set, repository = create_code_review_skill_tool_set(workspace_runtime=skill_workspace_runtime)
        tools.append(skill_tool_set)

    return LlmAgent(
        name="skills_code_review_agent",
        description=("Automatic code review agent backed by Skills, sandbox execution, "
                     "Filter governance, and SQLite persistence."),
        model=model,
        instruction=INSTRUCTION,
        tools=tools,
        skill_repository=repository,
    )
