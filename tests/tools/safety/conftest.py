# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared test fixtures for safety tests."""

from pathlib import Path

import pytest

from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
from trpc_agent_sdk.tools.safety._scanner import ToolSafetyScanner
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._types import RiskType
from trpc_agent_sdk.tools.safety._policy import PolicyRuleConfig
from trpc_agent_sdk.tools.safety._policy import WhitelistConfig
from trpc_agent_sdk.tools.safety._policy import BlocklistConfig


@pytest.fixture
def policy():
    return SafetyPolicy(
        version="1.0",
        max_script_size_bytes=1_048_576,
        max_scan_time_ms=5000,
        default_decision=Decision.DENY,
        rules=[
            PolicyRuleConfig(
                rule_id="DANGEROUS_DELETE_001",
                enabled=True,
                risk_type=RiskType.DANGEROUS_FILE_OP,
                severity=RiskLevel.CRITICAL,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="SENSITIVE_PATH_002",
                enabled=True,
                risk_type=RiskType.DANGEROUS_FILE_OP,
                severity=RiskLevel.CRITICAL,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="NETWORK_CURL_003",
                enabled=True,
                risk_type=RiskType.NETWORK_ACCESS,
                severity=RiskLevel.HIGH,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="NETWORK_PYTHON_004",
                enabled=True,
                risk_type=RiskType.NETWORK_ACCESS,
                severity=RiskLevel.HIGH,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="NETWORK_SOCKET_005",
                enabled=True,
                risk_type=RiskType.NETWORK_ACCESS,
                severity=RiskLevel.HIGH,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="SUBPROCESS_006",
                enabled=True,
                risk_type=RiskType.SYSTEM_COMMAND,
                severity=RiskLevel.HIGH,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="OS_SYSTEM_007",
                enabled=True,
                risk_type=RiskType.SYSTEM_COMMAND,
                severity=RiskLevel.HIGH,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="DEP_INSTALL_008",
                enabled=True,
                risk_type=RiskType.DEPENDENCY_INSTALL,
                severity=RiskLevel.HIGH,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="PRIVILEGE_ESCALA_009",
                enabled=True,
                risk_type=RiskType.SYSTEM_COMMAND,
                severity=RiskLevel.CRITICAL,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="SENSITIVE_LOG_010",
                enabled=True,
                risk_type=RiskType.SENSITIVE_INFO_LEAK,
                severity=RiskLevel.HIGH,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="FORK_BOMB_011",
                enabled=True,
                risk_type=RiskType.RESOURCE_ABUSE,
                severity=RiskLevel.CRITICAL,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="INFINITE_LOOP_012",
                enabled=True,
                risk_type=RiskType.RESOURCE_ABUSE,
                severity=RiskLevel.MEDIUM,
                decision=Decision.NEEDS_HUMAN_REVIEW,
            ),
            PolicyRuleConfig(
                rule_id="SYSTEM_COMMAND_013",
                enabled=True,
                risk_type=RiskType.SYSTEM_COMMAND,
                severity=RiskLevel.MEDIUM,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="SUBPROCESS_SHELL_001",
                enabled=True,
                risk_type=RiskType.SYSTEM_COMMAND,
                severity=RiskLevel.CRITICAL,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="NETWORK_AST_001",
                enabled=True,
                risk_type=RiskType.NETWORK_ACCESS,
                severity=RiskLevel.HIGH,
                decision=Decision.DENY,
            ),
            PolicyRuleConfig(
                rule_id="SENSITIVE_AST_001",
                enabled=True,
                risk_type=RiskType.SENSITIVE_INFO_LEAK,
                severity=RiskLevel.HIGH,
                decision=Decision.DENY,
            ),
        ],
        whitelist=WhitelistConfig(
            domains=["api.example.com", "trusted.internal.org", "localhost", "127.0.0.1"],
            commands=["ls", "cat", "echo", "pwd", "mkdir"],
            paths=["/tmp/", "/workspace/", "./"],
        ),
        blocklist=BlocklistConfig(
            paths=["~/.ssh", "~/.aws", "/etc/passwd", "/etc/shadow", ".env"],
            commands=["sudo", "chmod 777"],
        ),
    )


@pytest.fixture
def scanner(policy):
    return ToolSafetyScanner(policy=policy)


SAMPLES_DIR = Path(__file__).parent / "samples"


@pytest.fixture
def sample_scripts():
    scripts = {}
    for sample_file in SAMPLES_DIR.iterdir():
        if sample_file.is_file():
            scripts[sample_file.stem] = sample_file.read_text()
    return scripts
