"""Tests for bounded output collection in the local review sandbox."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from examples.skills_code_review_agent.agent.filtering import ReviewExecutionFilter
from examples.skills_code_review_agent.agent.models import SandboxRequest
from examples.skills_code_review_agent.agent.sandbox import SandboxRunner

SKILL_DIR = Path("examples/skills_code_review_agent/skills/code-review").resolve()
TRUNCATION_MARKER = "\n[output truncated]"


def _sandbox(*, max_output_bytes: int = 64) -> SandboxRunner:
    return SandboxRunner(
        runtime="dry-run-local",
        skill_dir=SKILL_DIR,
        execution_filter=ReviewExecutionFilter(
            max_timeout_seconds=5,
            max_output_bytes=max_output_bytes,
        ),
    )


def _request(**overrides: object) -> SandboxRequest:
    values: dict[str, object] = {
        "name": "bounded-local-run",
        "command": ["$PYTHON", "-c", "print('ok')"],
        "display_command": "python -c bounded-local-run",
        "cwd": ".",
        "timeout_seconds": 2.0,
        "max_output_bytes": 64,
    }
    values.update(overrides)
    return SandboxRequest(**values)


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_unbounded_stream_is_stopped_at_the_collection_budget(stream_name: str):
    code = (
        "import sys\n"
        "stream = getattr(sys, sys.argv[1]).buffer\n"
        "chunk = b'!' * 4096\n"
        "while True:\n"
        "    stream.write(chunk)\n"
        "    stream.flush()\n"
    )

    run = _sandbox().run(_request(command=["$PYTHON", "-c", code, stream_name]))

    assert run.status == "failed"
    assert run.error_type == "OutputLimitExceeded"
    assert run.timed_out is False
    assert run.output_truncated is True
    captured = getattr(run, stream_name)
    assert captured.endswith(TRUNCATION_MARKER)
    assert len(captured.removesuffix(TRUNCATION_MARKER).encode("utf-8")) <= 64
    other_stream = "stderr" if stream_name == "stdout" else "stdout"
    assert getattr(run, other_stream) == ""


def test_timeout_remains_structured_and_redacts_partial_output():
    secret = "ghp_abcdefghijklmnopqrstuvwxyz012345"
    code = "import sys, time\nprint(sys.argv[1], flush=True)\ntime.sleep(5)\n"

    run = _sandbox(max_output_bytes=128).run(
        _request(
            command=["$PYTHON", "-c", code, secret],
            timeout_seconds=0.1,
            max_output_bytes=128,
        )
    )

    assert run.status == "timed_out"
    assert run.error_type == "TimeoutExpired"
    assert run.exit_code is None
    assert run.timed_out is True
    assert secret not in run.stdout
    assert "<REDACTED>" in run.stdout


def test_output_limit_terminates_descendants(tmp_path: Path):
    marker = tmp_path / "descendant-survived.txt"
    descendant_code = (
        "import pathlib, sys, time; "
        "time.sleep(1); "
        "pathlib.Path(sys.argv[1]).write_text('survived', encoding='utf-8')"
    )
    parent_code = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]])\n"
        "while True:\n"
        "    print('!' * 4096, flush=True)\n"
    )

    run = _sandbox().run(
        _request(
            command=[
                "$PYTHON",
                "-c",
                parent_code,
                descendant_code,
                str(marker),
            ]
        )
    )

    assert run.error_type == "OutputLimitExceeded"
    time.sleep(1.2)
    assert not marker.exists()


def test_artifacts_use_bounded_reads_and_are_redacted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    secret = "ghp_abcdefghijklmnopqrstuvwxyz012345"
    workspace = tmp_path / "workspace"
    output_dir = workspace / "out"
    output_dir.mkdir(parents=True)
    (output_dir / "first.txt").write_text(
        "prefix token=" + secret + ("!" * 20000), encoding="utf-8"
    )
    (output_dir / "second.txt").write_text("!" * 20000, encoding="utf-8")
    request = _request(
        output_files=["out/first.txt", "out/second.txt"],
        max_output_bytes=64,
    )

    def reject_unbounded_read(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("artifact collection must not call Path.read_text")

    monkeypatch.setattr(Path, "read_text", reject_unbounded_read)
    artifacts, output_truncated = SandboxRunner._collect_outputs(workspace, request)

    assert output_truncated is True
    assert set(artifacts) == {"out/first.txt", "out/second.txt"}
    assert secret not in artifacts["out/first.txt"]
    assert "<REDACTED>" in artifacts["out/first.txt"]
    assert artifacts["out/second.txt"].endswith(TRUNCATION_MARKER)
    for content in artifacts.values():
        assert len(content.removesuffix(TRUNCATION_MARKER).encode("utf-8")) <= 64


def test_redaction_happens_before_the_visible_byte_slice():
    secret = "ghp_abcdefghijklmnopqrstuvwxyz012345"
    prefix = "p" * 60

    run = _sandbox().run(
        _request(
            command=[
                "$PYTHON",
                "-c",
                "print(__import__('sys').argv[1])",
                prefix + secret,
            ]
        )
    )

    assert run.status == "succeeded"
    assert run.output_truncated is True
    assert secret not in run.stdout
    assert run.stdout.endswith(TRUNCATION_MARKER)
    assert len(run.stdout.removesuffix(TRUNCATION_MARKER).encode("utf-8")) <= 64


def test_visible_prefix_does_not_split_a_utf8_code_point():
    visible, truncated = SandboxRunner._truncate("界", 2)

    assert truncated is True
    assert visible == TRUNCATION_MARKER
