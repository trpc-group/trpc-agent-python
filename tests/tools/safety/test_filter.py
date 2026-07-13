# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for ToolSafetyFilter integration."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.abc._filter import FilterResult
from trpc_agent_sdk.tools.safety._filter import ToolSafetyFilter
from trpc_agent_sdk.tools.safety._types import Decision
from trpc_agent_sdk.tools.safety._types import RiskLevel
from trpc_agent_sdk.tools.safety._types import RiskType
from trpc_agent_sdk.tools.safety._types import RuleFinding
from trpc_agent_sdk.tools.safety._types import ScanReport


@pytest.fixture
def mock_scanner():
    scanner = MagicMock()
    scanner.scan = AsyncMock()
    scanner.hash_script = MagicMock(return_value="abc123")
    return scanner


@pytest.fixture
def mock_audit_logger():
    logger = MagicMock()
    return logger


@pytest.fixture
def filter_instance(mock_scanner, mock_audit_logger):
    return ToolSafetyFilter(scanner=mock_scanner, audit_logger=mock_audit_logger)


class TestToolSafetyFilter:

    async def test_filter_blocks_on_deny(self, filter_instance, mock_scanner):
        mock_scanner.scan.return_value = ScanReport(
            decision=Decision.DENY,
            risk_level=RiskLevel.CRITICAL,
            findings=[
                RuleFinding(
                    rule_id="DANGEROUS_DELETE_001",
                    risk_type=RiskType.DANGEROUS_FILE_OP,
                    risk_level=RiskLevel.CRITICAL,
                    evidence="rm -rf /",
                    message="dangerous",
                    recommendation="stop",
                ),
            ],
            scan_duration_ms=10.0,
        )

        ctx = MagicMock()
        req = MagicMock()
        req.args = {"command": "rm -rf /"}
        req.tool_name = "bash_tool"
        rsp = FilterResult()

        await filter_instance._before(ctx, req, rsp)

        assert rsp.is_continue is False
        assert rsp.error is not None

    async def test_filter_passes_on_allow(self, filter_instance, mock_scanner):
        mock_scanner.scan.return_value = ScanReport(
            decision=Decision.ALLOW,
            findings=[],
            scan_duration_ms=1.0,
        )

        ctx = MagicMock()
        req = MagicMock()
        req.args = {"command": "ls -la"}
        req.tool_name = "bash_tool"
        rsp = FilterResult()

        await filter_instance._before(ctx, req, rsp)

        assert rsp.is_continue is True
        assert rsp.error is None

    async def test_filter_with_no_script_content_passes(self, filter_instance, mock_scanner):
        mock_scanner.scan = AsyncMock()

        ctx = MagicMock()
        req = MagicMock()
        req.args = {}
        req.tool_name = "todo_tool"
        rsp = FilterResult()

        await filter_instance._before(ctx, req, rsp)

        assert rsp.is_continue is True
        mock_scanner.scan.assert_not_called()

    async def test_audit_logger_called_on_scan(self, filter_instance, mock_scanner, mock_audit_logger):
        mock_scanner.scan.return_value = ScanReport(
            decision=Decision.ALLOW,
            findings=[],
            scan_duration_ms=1.0,
        )

        ctx = MagicMock()
        req = MagicMock()
        req.args = {"script": "print('hello')"}
        req.tool_name = "python_tool"
        rsp = FilterResult()

        await filter_instance._before(ctx, req, rsp)

        mock_audit_logger.log.assert_called_once()
