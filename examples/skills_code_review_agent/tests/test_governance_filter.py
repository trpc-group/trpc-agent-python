# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Filter governance (issue requirement 8, acceptance criterion 7):
deny / needs_human_review MUST prevent sandbox execution."""

import pytest

from codereview.config import PolicyConfig
from codereview.governance import ACTION_ALLOW
from codereview.governance import ACTION_DENY
from codereview.governance import ACTION_NEEDS_HUMAN_REVIEW
from codereview.governance import PolicyDecision
from codereview.governance import SandboxGovernanceFilter
from codereview.governance import SandboxRunRequest
from codereview.governance import gated_sandbox_run


class Recorder:
    def __init__(self):
        self.decisions = []
        self.handler_called = False

    def on_decision(self, req, decision):
        self.decisions.append((req, decision))

    async def handler(self):
        self.handler_called = True
        return "SANDBOX_RAN"


def _request(**overrides) -> SandboxRunRequest:
    base = dict(kind="static_checks", cmd="python3",
                args=["skills/code-review/scripts/run_checks.py"],
                script_host_path="", wants_network=False, est_timeout=10.0,
                run_index=0, total_sandbox_seconds=0.0)
    base.update(overrides)
    return SandboxRunRequest(**base)


@pytest.fixture
def recorder():
    return Recorder()


@pytest.fixture
def policy():
    return PolicyConfig()


async def test_allowed_run_reaches_handler(recorder, policy):
    governance = SandboxGovernanceFilter(policy, on_decision=recorder.on_decision)
    result = await gated_sandbox_run(_request(), governance, recorder.handler)
    assert result == "SANDBOX_RAN"
    assert recorder.handler_called
    assert recorder.decisions[-1][1].action == ACTION_ALLOW


async def test_risky_script_needs_human_review_and_never_executes(tmp_path, recorder, policy):
    risky = tmp_path / "danger.py"
    risky.write_text("import os\nos.system('rm -rf /')\n", encoding="utf-8")
    governance = SandboxGovernanceFilter(policy, on_decision=recorder.on_decision)
    result = await gated_sandbox_run(_request(script_host_path=str(risky)),
                                     governance, recorder.handler)
    assert isinstance(result, PolicyDecision)
    assert result.action == ACTION_NEEDS_HUMAN_REVIEW
    assert result.rule == "risky_script"
    assert "rm_rf" in result.reasons[0]
    assert recorder.handler_called is False  # the sandbox never ran


async def test_network_fetching_script_flagged(tmp_path, recorder, policy):
    fetcher = tmp_path / "fetch.py"
    fetcher.write_text("import urllib.request\nurllib.request.urlopen('https://evil.example')\n",
                       encoding="utf-8")
    governance = SandboxGovernanceFilter(policy, on_decision=recorder.on_decision)
    result = await gated_sandbox_run(_request(script_host_path=str(fetcher)),
                                     governance, recorder.handler)
    assert isinstance(result, PolicyDecision)
    assert result.action == ACTION_NEEDS_HUMAN_REVIEW
    assert not recorder.handler_called


async def test_non_whitelisted_command_denied(recorder, policy):
    governance = SandboxGovernanceFilter(policy, on_decision=recorder.on_decision)
    result = await gated_sandbox_run(_request(cmd="bash", args=["-c", "echo hi"]),
                                     governance, recorder.handler)
    assert isinstance(result, PolicyDecision)
    assert result.action == ACTION_DENY
    assert result.rule == "allowed_cmds"
    assert not recorder.handler_called


async def test_forbidden_path_denied(recorder, policy):
    governance = SandboxGovernanceFilter(policy, on_decision=recorder.on_decision)
    for bad_arg in ("/etc/passwd", "../outside.py", "~/secrets.txt", "/var/run/docker.sock"):
        recorder.handler_called = False
        result = await gated_sandbox_run(_request(args=[bad_arg]), governance, recorder.handler)
        assert isinstance(result, PolicyDecision), bad_arg
        assert result.action == ACTION_DENY, bad_arg
        assert result.rule == "forbidden_paths", bad_arg
        assert not recorder.handler_called


async def test_network_request_denied_when_not_whitelisted(recorder, policy):
    governance = SandboxGovernanceFilter(policy, on_decision=recorder.on_decision)
    result = await gated_sandbox_run(_request(wants_network=True), governance, recorder.handler)
    assert isinstance(result, PolicyDecision)
    assert result.action == ACTION_DENY
    assert result.rule == "network_whitelist"
    assert not recorder.handler_called


async def test_over_budget_needs_human_review(recorder, policy):
    governance = SandboxGovernanceFilter(policy, on_decision=recorder.on_decision)

    over_runs = await gated_sandbox_run(_request(run_index=policy.max_sandbox_runs),
                                        governance, recorder.handler)
    assert isinstance(over_runs, PolicyDecision)
    assert over_runs.action == ACTION_NEEDS_HUMAN_REVIEW
    assert over_runs.rule == "run_budget"

    over_time = await gated_sandbox_run(
        _request(total_sandbox_seconds=policy.max_total_sandbox_seconds, est_timeout=1.0),
        governance, recorder.handler)
    assert isinstance(over_time, PolicyDecision)
    assert over_time.action == ACTION_NEEDS_HUMAN_REVIEW
    assert over_time.rule == "time_budget"
    assert not recorder.handler_called


async def test_every_decision_recorded_with_reasons(recorder, policy):
    governance = SandboxGovernanceFilter(policy, on_decision=recorder.on_decision)
    await gated_sandbox_run(_request(), governance, recorder.handler)
    await gated_sandbox_run(_request(cmd="bash"), governance, recorder.handler)
    actions = [decision.action for _req, decision in recorder.decisions]
    assert actions == [ACTION_ALLOW, ACTION_DENY]
    denied = recorder.decisions[-1][1]
    assert denied.reasons and "bash" in denied.reasons[0]


async def test_pipeline_records_block_in_report_and_db(tmp_path):
    """E2E: a deny decision is written to the report AND the database, the
    sandbox never runs, and the review still completes via host fallback."""
    from codereview.config import ReviewConfig
    from codereview.config import SandboxConfig
    from .helpers import run_fixture

    config = ReviewConfig(
        db_url=f"sqlite+aiosqlite:///{tmp_path}/review.db",
        out_dir=str(tmp_path / "out"),
        model_mode="fake",
        sandbox=SandboxConfig(runtime_kind="local", work_root=str(tmp_path / "ws")),
        policy=PolicyConfig(allowed_cmds=("nothing-allowed",)),  # forces deny
    )
    run = await run_fixture("security_issue", tmp_path, config=config)
    try:
        assert run.result.status == "completed_with_errors"
        # block visible in metrics + report filter summary, with reasons
        assert run.report["metrics"]["filter_block_count"] == 1
        assert run.report["filter_summary"]["blocked"] == 1
        event = run.report["filter_summary"]["events"][0]
        assert event["action"] == ACTION_DENY
        assert event["rule"] == "allowed_cmds"
        assert event["reasons"]
        # the sandbox never executed: run row is blocked, run count stays 0
        assert run.report["sandbox_summary"]["runs"][0]["status"] == "blocked"
        assert run.report["metrics"]["sandbox_run_count"] == 0
        # host fallback still reviewed the diff (review must not die)
        assert run.report["findings"]
        # the same records are queryable from the DB by task id
        events = await run.store.get_filter_events(run.result.task_id)
        assert events and events[0]["action"] == ACTION_DENY and events[0]["reasons"]
        runs = await run.store.get_sandbox_runs(run.result.task_id)
        assert runs[0]["status"] == "blocked"
        assert runs[0]["filter_action"] == ACTION_DENY
        # the gate saw (and vetoed) the COMPLETE sandbox argv, not just the
        # entry script — diff input and findings output args included
        assert "work/inputs/diff.json" in runs[0]["args"]
        assert "out/findings.json" in runs[0]["args"]
        # markdown报告展示拦截表格
        assert "**deny**" in run.report_md
    finally:
        await run.store.close()


async def test_own_skill_scripts_pass_the_gate(policy):
    """The shipped run_checks.py/parse_diff.py must themselves be clean."""
    from codereview.config import SKILL_NAME
    from codereview.config import SKILLS_ROOT
    import os
    recorder = Recorder()
    governance = SandboxGovernanceFilter(policy, on_decision=recorder.on_decision)
    for script in ("run_checks.py", "parse_diff.py"):
        recorder.handler_called = False
        path = os.path.join(SKILLS_ROOT, SKILL_NAME, "scripts", script)
        result = await gated_sandbox_run(_request(script_host_path=path),
                                         governance, recorder.handler)
        assert result == "SANDBOX_RAN", f"{script} was blocked: {result}"
