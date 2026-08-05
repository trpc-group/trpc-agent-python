#!/usr/bin/env python3
"""Run a real Docker-backed Skill check without calling a model API."""

import asyncio
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import Mock

EXAMPLE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(EXAMPLE_ROOT))

from agent.tools import create_skill_tools
from filters.sdk_filter import FILTER_DECISIONS_METADATA_KEY
from sandbox.docker import DockerSandbox
from trpc_agent_sdk.abc import AgentABC
from trpc_agent_sdk.code_executors import WorkspaceRunProgramSpec
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import new_agent_context
from trpc_agent_sdk.sessions import InMemorySessionService


async def run() -> dict[str, object]:
    """Create isolated test inputs and exercise the Docker-backed Skill."""
    with tempfile.TemporaryDirectory() as directory:
        input_root = Path(directory)
        security_fixture = (
            EXAMPLE_ROOT / "tests" / "fixtures" / "security.diff"
        )
        (input_root / "security.diff").write_text(
            security_fixture.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        large_lines = "".join(
            f"+value_{index} = {index}\n" for index in range(800)
        )
        (input_root / "large-output.diff").write_text(
            "diff --git a/large.py b/large.py\n"
            "--- /dev/null\n"
            "+++ b/large.py\n"
            "@@ -0,0 +1,800 @@\n"
            + large_lines,
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "init", "--quiet", str(input_root)],
            check=True,
            capture_output=True,
        )
        tracked = input_root / "tracked.py"
        tracked.write_text("def run(value):\n    return value\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(input_root), "add", "tracked.py"],
            check=True,
            capture_output=True,
        )
        tracked.write_text(
            "import os\n\ndef run(value):\n    return os.system(value)\n",
            encoding="utf-8",
        )
        (input_root / "untracked.py").write_text(
            "value = 1\n",
            encoding="utf-8",
        )
        return await _run(input_root)


async def _run(input_root: Path) -> dict[str, object]:
    """Load the Skill and execute its parser through the governed runtime."""
    output_limit = 64 * 1024
    phase_durations: dict[str, float] = {}
    sandbox = DockerSandbox(output_limit_bytes=output_limit)
    toolset, _repository, runtime = create_skill_tools(
        sandbox,
        input_root,
        EXAMPLE_ROOT / "skills",
    )
    service = InMemorySessionService()
    session = await service.create_session(
        app_name="skills_code_review_agent_docker_test",
        user_id="docker-test-user",
        session_id="docker-test-session",
    )
    agent = Mock(spec=AgentABC)
    agent.name = "docker_test_agent"
    agent.before_tool_callback = None
    agent.after_tool_callback = None
    agent_context = new_agent_context()
    invocation = InvocationContext(
        session_service=service,
        invocation_id="docker-test-invocation",
        agent=agent,
        agent_context=agent_context,
        session=session,
    )
    tools = {
        tool.name: tool for tool in await toolset.get_tools(invocation)
    }
    await tools["skill_load"].run_async(
        tool_context=invocation,
        args={"skill_name": "code-review", "include_all_docs": True},
    )
    result = await tools["skill_run"].run_async(
        tool_context=invocation,
        args={
            "skill": "code-review",
            "command": (
                "python3 scripts/run_review_rules.py "
                "work/inputs/security.diff"
            ),
        },
    )
    if result.get("exit_code") != 0:
        raise RuntimeError(f"Docker Skill run failed: {result.get('stderr', '')}")
    parsed = json.loads(result.get("stdout", "{}"))
    decisions = agent_context.get_metadata(FILTER_DECISIONS_METADATA_KEY, [])
    if not decisions or decisions[-1]["decision"] != "allow":
        raise RuntimeError("Filter did not record an allow decision")
    if parsed.get("summary", {}).get("file_count") != 1:
        raise RuntimeError("Sandbox parser returned an unexpected result")
    categories = {
        item["category"]
        for item in parsed.get("records", [])
        if item.get("type") == "finding"
    }
    if "security" not in categories:
        raise RuntimeError("Sandbox rule runner missed the security fixture")
    large_result = await tools["skill_run"].run_async(
        tool_context=invocation,
        args={
            "skill": "code-review",
            "command": (
                "python3 scripts/run_review_rules.py "
                "work/inputs/large-output.diff"
            ),
        },
    )
    large_page = json.loads(large_result.get("stdout", "{}"))
    pagination_safe = (
        large_page.get("next_cursor") is not None
        and len(large_result.get("stdout", "")) < 16 * 1024
        and not large_result.get("warnings")
    )
    if not pagination_safe:
        raise RuntimeError("Docker Skill pagination exceeded the inline output limit")
    git_files_result = await tools["skill_run"].run_async(
        tool_context=invocation,
        args={
            "skill": "code-review",
            "command": (
                "python3 scripts/inspect_git_files.py "
                "work/inputs --mode changed"
            ),
        },
    )
    git_files_page = json.loads(git_files_result.get("stdout", "{}"))
    git_paths = {
        item.get("path") for item in git_files_page.get("records", [])
    }
    if not {"tracked.py", "untracked.py"} <= git_paths:
        raise RuntimeError("Docker Git file enumeration missed changed files")
    if not git_files_page.get("input_digest"):
        raise RuntimeError("Docker Git file enumeration omitted its input digest")

    controlled_read = await tools["skill_run"].run_async(
        tool_context=invocation,
        args={
            "skill": "code-review",
            "command": (
                "python3 scripts/inspect_files.py work/inputs "
                "--scope changed --path tracked.py"
            ),
        },
    )
    if controlled_read.get("exit_code") != 0:
        raise RuntimeError("Controlled reader rejected an in-scope changed file")
    controlled_payload = json.loads(controlled_read.get("stdout", "{}"))
    if controlled_payload.get("files", [{}])[0].get("path") != "tracked.py":
        raise RuntimeError("Controlled reader returned an unexpected path")
    outside_scope = await tools["skill_run"].run_async(
        tool_context=invocation,
        args={
            "skill": "code-review",
            "command": (
                "python3 scripts/inspect_files.py work/inputs "
                "--scope changed --path .git/config"
            ),
        },
    )
    if outside_scope.get("exit_code") == 0:
        raise RuntimeError("Controlled reader allowed a path outside Git scope")

    git_diff_result = await tools["skill_run"].run_async(
        tool_context=invocation,
        args={
            "skill": "code-review",
            "command": (
                "python3 scripts/review_git_changes.py "
                "work/inputs --mode unstaged"
            ),
        },
    )
    git_diff_page = json.loads(git_diff_result.get("stdout", "{}"))
    git_categories = {
        item.get("category")
        for item in git_diff_page.get("records", [])
        if item.get("type") == "finding"
    }
    if "security" not in git_categories or not git_diff_page.get("input_digest"):
        raise RuntimeError("Docker Git diff review missed expected evidence")
    workspace = await runtime.manager(invocation).create_workspace(
        session.id,
        invocation,
    )
    phase_started = time.perf_counter()
    timeout_result = await runtime.runner(invocation).run_program(
        workspace,
        WorkspaceRunProgramSpec(
            cmd="python3",
            args=[
                "-c",
                (
                    "import time; from pathlib import Path; time.sleep(2); "
                    "Path('/tmp/code-review-timeout-marker').write_text('late')"
                ),
            ],
            cwd=".",
            timeout=0.1,
        ),
        invocation,
    )
    phase_durations["timeout_run_ms"] = (time.perf_counter() - phase_started) * 1000
    if not timeout_result.timed_out:
        raise RuntimeError("Docker runtime did not enforce the timeout")
    await asyncio.sleep(2.1)
    phase_started = time.perf_counter()
    timeout_marker = await runtime.runner(invocation).run_program(
        workspace,
        WorkspaceRunProgramSpec(
            cmd="python3",
            args=[
                "-c",
                (
                    "from pathlib import Path; "
                    "print(Path('/tmp/code-review-timeout-marker').exists())"
                ),
            ],
            cwd=".",
            timeout=2,
        ),
        invocation,
    )
    phase_durations["timeout_check_ms"] = (time.perf_counter() - phase_started) * 1000
    if timeout_marker.stdout.strip() != "False":
        raise RuntimeError("Timed-out Docker process continued running")

    phase_started = time.perf_counter()
    bounded_output = await runtime.runner(invocation).run_program(
        workspace,
        WorkspaceRunProgramSpec(
            cmd="python3",
            args=[
                "-c",
                (
                    "print('API_KEY=sk-testabcdefghijklmnop'); "
                    f"print('A' * {output_limit * 2})"
                ),
            ],
            cwd=".",
            timeout=2,
        ),
        invocation,
    )
    phase_durations["bounded_output_ms"] = (time.perf_counter() - phase_started) * 1000
    if phase_durations["bounded_output_ms"] > 10_000:
        raise RuntimeError("Bounded Docker output exceeded its execution budget")
    combined_output = bounded_output.stdout + bounded_output.stderr
    if "sk-testabcdefghijklmnop" in combined_output:
        raise RuntimeError("Sandbox output was not redacted before returning")
    if len(combined_output) > output_limit:
        raise RuntimeError("Sandbox output exceeded its configured hard limit")
    phase_started = time.perf_counter()
    write_result = await runtime.runner(invocation).run_program(
        workspace,
        WorkspaceRunProgramSpec(
            cmd="python3",
            args=[
                "-c",
                (
                    "from pathlib import Path; "
                    "Path('/opt/trpc-agent/inputs/security.diff')"
                    ".write_text('unexpected')"
                ),
            ],
            cwd=".",
            timeout=2,
        ),
        invocation,
    )
    phase_durations["read_only_check_ms"] = (time.perf_counter() - phase_started) * 1000
    if write_result.exit_code == 0:
        raise RuntimeError("Docker input mount is unexpectedly writable")
    backing_runtime = runtime._runtime.runtime
    container_attributes = backing_runtime.container.container.attrs
    host = container_attributes.get("HostConfig", {})
    config = container_attributes.get("Config", {})
    hardened = (
        host.get("ReadonlyRootfs") is True
        and "ALL" in (host.get("CapDrop") or [])
        and host.get("Memory", 0) > 0
        and host.get("NanoCpus", 0) > 0
        and host.get("PidsLimit", 0) > 0
        and host.get("NetworkMode") == "none"
        and bool(host.get("SecurityOpt"))
        and config.get("User") not in {"", "0", "0:0"}
    )
    if not hardened:
        raise RuntimeError("Docker container security profile is incomplete")
    result_summary = {
        "runtime_initialized": runtime.is_initialized,
        "isolation": runtime.describe().isolation,
        "inputs_read_only": write_result.exit_code != 0,
        "network_allowed": runtime.describe().network_allowed,
        "pagination_safe": pagination_safe,
        "git_file_pagination": bool(git_files_page.get("input_digest")),
        "git_diff_pagination": bool(git_diff_page.get("input_digest")),
        "git_scope_enforced": outside_scope.get("exit_code") != 0,
        "hardened_container": hardened,
        "filter_decision": decisions[-1]["decision"],
        "exit_code": result["exit_code"],
        "file_count": parsed["summary"]["file_count"],
        "rule_categories": sorted(categories),
        "timeout_enforced": timeout_result.timed_out,
        "timed_out_process_stopped": timeout_marker.stdout.strip() == "False",
        "output_limit_enforced": len(combined_output) <= output_limit,
        "output_redacted": "sk-testabcdefghijklmnop" not in combined_output,
        "phase_durations_ms": phase_durations,
    }
    await toolset.close()
    return result_summary


def main() -> int:
    print(json.dumps(asyncio.run(run()), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
