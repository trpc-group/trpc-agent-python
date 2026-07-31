# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for example-local execution governance."""

from __future__ import annotations

from pathlib import Path

from examples.skills_code_review_agent.agent import ExecutionRequest
from examples.skills_code_review_agent.agent import FilterDecision
from examples.skills_code_review_agent.agent import FilterReasonCode
from examples.skills_code_review_agent.agent import RuntimeKind
from examples.skills_code_review_agent.agent import evaluate_execution_request
from examples.skills_code_review_agent.tests.secret_samples import openai_live_like_token


def test_dry_run_rule_runner_command_is_allowed(tmp_path: Path):
    request = _request(tmp_path, RuntimeKind.DRY_RUN)

    event = evaluate_execution_request("task", request)

    assert event.decision is FilterDecision.ALLOW


def test_local_dev_requires_explicit_allow_local(tmp_path: Path):
    event = evaluate_execution_request("task", _request(tmp_path, RuntimeKind.LOCAL_DEV, allow_local=False))

    assert event.decision is FilterDecision.DENY
    assert event.reason_code is FilterReasonCode.LOCAL_RUNTIME_DENIED


def test_local_dev_is_allowed_when_explicit(tmp_path: Path):
    event = evaluate_execution_request("task", _request(tmp_path, RuntimeKind.LOCAL_DEV, allow_local=True))

    assert event.decision is FilterDecision.ALLOW


def test_container_and_cube_need_human_review(tmp_path: Path):
    for runtime in (RuntimeKind.CONTAINER, RuntimeKind.CUBE):
        event = evaluate_execution_request("task", _request(tmp_path, runtime))
        assert event.decision is FilterDecision.ALLOW


def test_dangerous_command_is_denied(tmp_path: Path):
    request = _request(tmp_path, RuntimeKind.DRY_RUN, command=["rm", "-rf", str(tmp_path)])

    event = evaluate_execution_request("task", request)

    assert event.decision is FilterDecision.DENY
    assert event.reason_code is FilterReasonCode.HIGH_RISK_COMMAND


def test_recursive_flag_variants_are_denied(tmp_path: Path):
    rm_event = evaluate_execution_request(
        "task",
        _request(tmp_path, RuntimeKind.DRY_RUN, command=["rm", "--recursive", "--force",
                                                         str(tmp_path)]),
    )
    chmod_event = evaluate_execution_request(
        "task",
        _request(tmp_path, RuntimeKind.DRY_RUN, command=["chmod", "--recursive", "777",
                                                         str(tmp_path)]),
    )

    assert rm_event.reason_code is FilterReasonCode.HIGH_RISK_COMMAND
    assert chmod_event.reason_code is FilterReasonCode.HIGH_RISK_COMMAND


def test_network_command_needs_human_review(tmp_path: Path):
    request = _request(tmp_path, RuntimeKind.DRY_RUN, command=["curl", "https://example.com"])

    event = evaluate_execution_request("task", request)

    assert event.decision is FilterDecision.NEEDS_HUMAN_REVIEW
    assert event.reason_code is FilterReasonCode.NETWORK_DENIED


def test_argv_aware_network_detection_reduces_false_positives(tmp_path: Path):
    binary_event = evaluate_execution_request(
        "task",
        _request(tmp_path, RuntimeKind.DRY_RUN, command=["/usr/bin/curl", "https://example.com"]),
    )
    literal_event = evaluate_execution_request(
        "task",
        _request(tmp_path, RuntimeKind.DRY_RUN, command=["echo", "curl"]),
    )
    shell_event = evaluate_execution_request(
        "task",
        _request(tmp_path, RuntimeKind.DRY_RUN, command=["bash", "-lc", "curl https://example.com"]),
    )

    assert binary_event.reason_code is FilterReasonCode.NETWORK_DENIED
    assert literal_event.decision is FilterDecision.ALLOW
    assert shell_event.reason_code is FilterReasonCode.NETWORK_DENIED


def test_forbidden_path_is_denied(tmp_path: Path):
    request = ExecutionRequest(
        command=["python", "rule_runner.py"],
        runtime=RuntimeKind.DRY_RUN,
        cwd="/etc",
        script_path=str(tmp_path / "rule_runner.py"),
        allowed_roots=(str(tmp_path), ),
    )

    event = evaluate_execution_request("task", request)

    assert event.decision is FilterDecision.DENY
    assert event.reason_code is FilterReasonCode.FORBIDDEN_PATH


def test_referenced_paths_must_stay_within_allowed_roots(tmp_path: Path):
    outside_input = tmp_path.parent / "outside-input.json"
    outside_manifest = tmp_path.parent / "outside-manifest.json"
    outside_output = tmp_path.parent / "outside-output.json"

    input_event = evaluate_execution_request(
        "task",
        _request(tmp_path, RuntimeKind.DRY_RUN, referenced_paths=(str(outside_input), )),
    )
    manifest_event = evaluate_execution_request(
        "task",
        _request(tmp_path, RuntimeKind.DRY_RUN, referenced_paths=(str(tmp_path / "in.json"), str(outside_manifest))),
    )
    output_event = evaluate_execution_request(
        "task",
        _request(
            tmp_path,
            RuntimeKind.DRY_RUN,
            referenced_paths=(str(tmp_path / "in.json"), str(tmp_path / "manifest.json"), str(outside_output)),
        ),
    )

    assert input_event.reason_code is FilterReasonCode.FORBIDDEN_PATH
    assert manifest_event.reason_code is FilterReasonCode.FORBIDDEN_PATH
    assert output_event.reason_code is FilterReasonCode.FORBIDDEN_PATH


def test_timeout_and_output_budget_are_denied(tmp_path: Path):
    timeout_event = evaluate_execution_request("task", _request(tmp_path, RuntimeKind.DRY_RUN, timeout_sec=121))
    output_event = evaluate_execution_request("task", _request(tmp_path, RuntimeKind.DRY_RUN, output_limit_bytes=0))

    assert timeout_event.reason_code is FilterReasonCode.BUDGET_EXCEEDED
    assert output_event.reason_code is FilterReasonCode.OUTPUT_LIMIT_EXCEEDED


def test_environment_allowlist_is_enforced_and_redacted(tmp_path: Path):
    allowed = evaluate_execution_request("task", _request(tmp_path, RuntimeKind.DRY_RUN, env={"PATH": "/usr/bin"}))
    denied = evaluate_execution_request(
        "task",
        _request(tmp_path, RuntimeKind.DRY_RUN, env={"API_KEY": openai_live_like_token()}),
    )

    assert allowed.decision is FilterDecision.ALLOW
    assert denied.decision is FilterDecision.DENY
    assert denied.reason_code is FilterReasonCode.ENV_NOT_ALLOWED
    assert "secretvalue" not in str(denied.to_dict())


def _request(
        tmp_path: Path,
        runtime: RuntimeKind,
        *,
        command: list[str] | None = None,
        allow_local: bool = False,
        timeout_sec: float = 30,
        output_limit_bytes: int = 65536,
        env: dict[str, str] | None = None,
        referenced_paths: tuple[str, ...] = (),
) -> ExecutionRequest:
    return ExecutionRequest(
        command=command or ["python", "rule_runner.py"],
        runtime=runtime,
        cwd=str(tmp_path),
        script_path=str(tmp_path / "rule_runner.py"),
        allowed_roots=(str(tmp_path), ),
        allow_local=allow_local,
        timeout_sec=timeout_sec,
        output_limit_bytes=output_limit_bytes,
        env=env or {},
        referenced_paths=referenced_paths,
    )
