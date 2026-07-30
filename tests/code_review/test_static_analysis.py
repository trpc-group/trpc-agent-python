"""Static analyzer parsing, execution, and Docker hardening tests."""

from pathlib import Path

import pytest

from examples.code_review_agent.code_review.git_diff import GitDiffCollector
from examples.code_review_agent.code_review.models import AnalyzerStatus, Severity
from examples.code_review_agent.code_review.static_analysis import (
    CommandResult,
    DockerCommandRunner,
    StaticAnalysisConfig,
    StaticAnalyzer,
    parse_bandit_output,
    parse_ruff_output,
)

RUFF_JSON = """[
  {
    "code": "F821",
    "message": "Undefined name `missing`",
    "filename": "app.py",
    "location": {"row": 3, "column": 12},
    "end_location": {"row": 3, "column": 19},
    "fix": null
  }
]"""

BANDIT_JSON = """{
  "results": [
    {
      "test_id": "B602",
      "issue_text": "subprocess call with shell=True",
      "issue_severity": "HIGH",
      "issue_confidence": "HIGH",
      "filename": "app.py",
      "line_number": 3,
      "line_range": [3],
      "more_info": "https://bandit.readthedocs.io/"
    }
  ]
}"""


def test_parses_ruff_and_bandit_findings(tmp_path: Path) -> None:
    ruff = parse_ruff_output(RUFF_JSON, tmp_path)
    bandit = parse_bandit_output(BANDIT_JSON, tmp_path)

    assert ruff[0].rule_id == "ruff.f821"
    assert ruff[0].severity == Severity.MEDIUM
    assert ruff[0].file_path == "app.py"
    assert bandit[0].rule_id == "bandit.b602"
    assert bandit[0].severity == Severity.HIGH
    assert bandit[0].confidence == 0.95


class _FakeRunner:
    runtime_name = "fake"

    def run(self, command: list[str], *, repository: Path, timeout: float) -> CommandResult:
        del repository, timeout
        if command[0] == "ruff":
            return CommandResult(command=command, exit_code=1, stdout=RUFF_JSON)
        if command[0] == "bandit":
            return CommandResult(command=command, exit_code=1, stdout=BANDIT_JSON)
        return CommandResult(command=command, exit_code=0, stdout="10 passed")


@pytest.mark.asyncio
async def test_runs_enabled_tools_only_on_changed_python_files(sample_repository: tuple[Path, str, str], ) -> None:
    repository, base, head = sample_repository
    _, changed_files = GitDiffCollector(repository).collect(base, head)
    analyzer = StaticAnalyzer(
        StaticAnalysisConfig(run_tests=True),
        command_runner=_FakeRunner(),
    )

    result = await analyzer.analyze(repository, changed_files)

    assert [execution.tool for execution in result.executions] == ["ruff", "bandit", "pytest"]
    assert [execution.status for execution in result.executions] == [
        AnalyzerStatus.FINDINGS,
        AnalyzerStatus.FINDINGS,
        AnalyzerStatus.SUCCESS,
    ]
    assert len(result.findings) == 2
    assert all("README.md" not in execution.command for execution in result.executions[:2])
    bandit_command = result.executions[1].command
    assert bandit_command[bandit_command.index("--") + 1:] == ["app.py"]


def test_docker_command_is_offline_read_only_and_resource_limited(tmp_path: Path) -> None:
    runner = DockerCommandRunner(
        image="review:test",
        memory="256m",
        cpus="0.5",
        pids_limit=64,
    )

    command = runner.build_command(["ruff", "check", "app.py"], tmp_path.resolve())
    joined = " ".join(command)

    assert "--network none" in joined
    assert "--read-only" in command
    assert "--cap-drop ALL" in joined
    assert "--security-opt no-new-privileges" in joined
    assert "--memory 256m" in joined
    assert "--cpus 0.5" in joined
    assert "readonly" in joined
    assert command[-3:] == ["ruff", "check", "app.py"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("docker_image", "--privileged"),
        ("docker_image", "review image"),
        ("docker_memory", "-1g"),
        ("docker_cpus", "0"),
        ("docker_pids_limit", 0),
    ],
)
def test_rejects_unsafe_docker_configuration(field: str, value: str | int) -> None:
    with pytest.raises(ValueError):
        StaticAnalysisConfig(**{field: value})


def test_missing_local_tool_is_non_fatal_by_default(sample_repository: tuple[Path, str, str], ) -> None:
    repository, base, head = sample_repository
    _, changed_files = GitDiffCollector(repository).collect(base, head)

    class MissingRunner:
        runtime_name = "local"

        def run(self, command: list[str], *, repository: Path, timeout: float) -> CommandResult:
            del repository, timeout
            return CommandResult(command=command, exit_code=None, stderr="not found", unavailable=True)

    analyzer = StaticAnalyzer(StaticAnalysisConfig(), command_runner=MissingRunner())
    result = analyzer.analyze_sync(repository, changed_files)

    assert [execution.status
            for execution in result.executions] == [AnalyzerStatus.UNAVAILABLE, AnalyzerStatus.UNAVAILABLE]
    assert len(result.diagnostics) == 2
