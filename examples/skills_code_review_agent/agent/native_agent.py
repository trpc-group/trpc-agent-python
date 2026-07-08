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
    repo_path: str | None = None,
    fixture: str | None = None,
    output_dir: str | None = None,
    db_path: str | None = None,
    db_url: str | None = None,
    dry_run: bool = True,
    container_image: str = "python:3-slim",
    include_network_scanners: bool = False,
) -> dict[str, Any]:
    """Run the Skills code review pipeline as a FunctionTool-compatible callable."""
    sandbox_runner = None
    sandbox = "fake"
    if not dry_run:
        from .runtime_factory import create_container_sandbox_runner

        sandbox = "container"
        sandbox_runner = create_container_sandbox_runner(image=container_image)
    report = await run_review(
        diff_file=Path(diff_file) if diff_file else None,
        repo_path=Path(repo_path) if repo_path else None,
        fixture=fixture,
        output_dir=Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR,
        db_path=Path(db_path) if db_path else DEFAULT_DB_PATH,
        db_url=db_url,
        dry_run=dry_run,
        sandbox=sandbox,
        sandbox_runner=sandbox_runner,
        include_network_scanners=include_network_scanners,
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
        description=(
            "Automatic code review agent backed by Skills, sandbox execution, "
            "Filter governance, and SQLite persistence."
        ),
        model=model,
        instruction=INSTRUCTION,
        tools=tools,
        skill_repository=repository,
    )
