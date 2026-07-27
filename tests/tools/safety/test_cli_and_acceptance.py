# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Public sample corpus and CLI acceptance tests."""

import asyncio
import importlib
import sys
from pathlib import Path

import pytest
import yaml

from trpc_agent_sdk.tools.safety import adapt_cli_request
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ScriptPayload
from trpc_agent_sdk.tools.safety import ToolMetadata
from trpc_agent_sdk.tools.safety import ToolScriptSafetyGuard
from trpc_agent_sdk.tools.safety._cli import main
from trpc_agent_sdk.tools.safety._cli import _exit_code
from trpc_agent_sdk.tools.safety._audit import SafetyAuditError
from trpc_agent_sdk.tools.safety import SafetyDecision

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLE_DIR = REPO_ROOT / "examples/tool_safety_guard"
sys.path.insert(0, str(REPO_ROOT))

mcp_server = importlib.import_module("examples.tool_safety_guard.mcp_server")


def _results():
    manifest = yaml.safe_load((EXAMPLE_DIR / "manifest.yaml").read_text(encoding="utf-8"))
    guard = ToolScriptSafetyGuard.from_policy(str(EXAMPLE_DIR / "tool_safety_policy.yaml"))
    results = {}
    for item in manifest["samples"]:
        path = EXAMPLE_DIR / item["file"]
        payload = ScriptPayload(
            language=ScriptLanguage(item["language"]),
            content=path.read_text(encoding="utf-8"),
            source=str(path),
        )
        request = adapt_cli_request(payload, ToolMetadata(name="acceptance"), guard.policy, str(EXAMPLE_DIR))
        results[item["file"]] = (item, guard.scan(request))
    return results


def test_public_sample_decisions_and_rates():
    results = _results()
    assert len(results) == 12
    for item, report in results.values():
        assert report.decision.value == item["expected"]

    safe = [report for item, report in results.values() if item.get("safe")]
    dangerous = [report for item, report in results.values() if not item.get("safe")]
    false_positive_rate = sum(report.decision.value != "allow" for report in safe) / len(safe)
    detection_rate = sum(report.decision.value != "allow" for report in dangerous) / len(dangerous)
    assert false_positive_rate <= 0.10
    assert detection_rate >= 0.90


def test_mandatory_categories_have_full_detection():
    results = _results()
    mandatory = [
        "samples/danger_delete.py",
        "samples/danger_ssh.py",
        "samples/danger_network.py",
    ]
    assert all(results[name][1].decision.value == "deny" for name in mandatory)


def test_cli_writes_report_and_audit(tmp_path, capsys):
    report = tmp_path / "report.json"
    audit = tmp_path / "audit.jsonl"
    exit_code = main([
        "--file",
        str(EXAMPLE_DIR / "samples/danger_delete.py"),
        "--language",
        "python",
        "--policy",
        str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
        "--report",
        str(report),
        "--audit",
        str(audit),
    ])
    assert exit_code == 3
    assert '"decision": "deny"' in report.read_text(encoding="utf-8")
    assert '"execution_blocked":true' in audit.read_text(encoding="utf-8")
    assert '"decision": "deny"' in capsys.readouterr().out


def test_cli_scans_argv_and_accepts_metadata(tmp_path):
    report = tmp_path / "report.json"
    exit_code = main([
        "--command",
        "echo ok",
        "--language",
        "bash",
        "--policy",
        str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
        "--report",
        str(report),
        "--argv",
        "~/.ssh/id_rsa",
        "--env-key",
        "TOKEN",
        "--tool-description",
        "MCP command",
        "--tag",
        "mcp",
    ])
    assert exit_code == 3


def test_cli_error_redacts_secret_path(tmp_path, capsys):
    missing = tmp_path / "password='top secret phrase'" / "missing.py"
    exit_code = main([
        "--file",
        str(missing),
        "--language",
        "python",
        "--policy",
        str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
    ])
    output = capsys.readouterr().out
    assert exit_code == 1
    assert "top secret phrase" not in output
    assert "[REDACTED_SECRET]" in output


def test_cli_audit_failure_returns_structured_error(monkeypatch, capsys):

    def fail_audit(*args, **kwargs):
        del args, kwargs
        raise SafetyAuditError("audit unavailable")

    monkeypatch.setattr("trpc_agent_sdk.tools.safety._cli.emit_report", fail_audit)
    exit_code = main([
        "--command",
        "echo ok",
        "--language",
        "bash",
        "--policy",
        str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
        "--audit",
        "audit.jsonl",
    ])
    output = capsys.readouterr().out
    assert exit_code == 1
    assert '"error"' in output
    assert "Traceback" not in output


def test_cli_exit_codes_cover_allow_and_review():
    assert _exit_code(SafetyDecision.ALLOW) == 0
    assert _exit_code(SafetyDecision.NEEDS_HUMAN_REVIEW) == 2


@pytest.mark.asyncio
async def test_mcp_timeout_reap_is_bounded(monkeypatch):

    class _HungProcess:
        returncode = None
        killed = False
        communicate_calls = 0

        async def communicate(self):
            self.communicate_calls += 1
            await asyncio.sleep(60)

        async def wait(self):
            await asyncio.sleep(60)

        def kill(self):
            self.killed = True

    process = _HungProcess()
    limited = []

    async def create_process(*args, **kwargs):
        del args, kwargs
        return process

    original_limit_output = mcp_server.GUARD.limit_output

    def track_limit_output(response):
        limited.append(response)
        return original_limit_output(response)

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(mcp_server, "PROCESS_REAP_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(mcp_server.GUARD, "limit_output", track_limit_output)
    result = await mcp_server.execute_command("echo ok", timeout=0.01)
    assert process.killed is True
    assert process.communicate_calls == 2
    assert result["timed_out"] is True
    assert result["reap_timed_out"] is True
    assert limited == [result]


@pytest.mark.asyncio
async def test_mcp_timeout_handles_reap_communicate_error(monkeypatch):

    class _FailedReapProcess:
        returncode = None
        killed = False
        communicate_calls = 0

        async def communicate(self):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                await asyncio.sleep(60)
            raise OSError("pipe already closing")

        def kill(self):
            self.killed = True

    process = _FailedReapProcess()

    async def create_process(*args, **kwargs):
        del args, kwargs
        return process

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", create_process)
    result = await mcp_server.execute_command("echo ok", timeout=0.01)
    assert process.killed is True
    assert result["timed_out"] is True
    assert result["reap_timed_out"] is True


@pytest.mark.asyncio
async def test_mcp_timeout_reaps_real_subprocess(monkeypatch):
    processes = []
    create_subprocess_exec = mcp_server.asyncio.create_subprocess_exec

    async def track_process(*args, **kwargs):
        process = await create_subprocess_exec(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", track_process)
    result = await mcp_server.execute_command(
        'python -c "__import__(\'threading\').Event().wait(60)"',
        timeout=0.05,
    )
    assert result["timed_out"] is True
    assert result["reap_timed_out"] is False
    assert len(processes) == 1
    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_mcp_output_redacts_secret_values(monkeypatch):

    class _SecretProcess:
        returncode = 0

        async def communicate(self):
            return (
                b"token=abcdefghijklmnopqrstuvwxyz",
                b"password='top secret phrase'",
            )

    async def create_process(*args, **kwargs):
        del args, kwargs
        return _SecretProcess()

    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", create_process)
    result = await mcp_server.execute_command("echo ok")

    assert result["redacted"] is True
    assert "abcdefghijklmnopqrstuvwxyz" not in result["stdout"]
    assert "top secret phrase" not in result["stderr"]
    assert "[REDACTED_SECRET]" in result["stdout"]
    assert "[REDACTED_SECRET]" in result["stderr"]


@pytest.mark.asyncio
async def test_mcp_subprocess_env_filters_sensitive_values(monkeypatch):

    class _EnvProcess:
        returncode = 0

        async def communicate(self):
            return (b"ok", b"")

    captured = {}

    async def create_process(*args, **kwargs):
        del args
        captured.update(kwargs.get("env", {}))
        return _EnvProcess()

    monkeypatch.setenv("TRPC_AGENT_API_KEY", "secret-key")
    monkeypatch.setenv("PATH", "safe-path")
    monkeypatch.setattr(mcp_server.asyncio, "create_subprocess_exec", create_process)
    result = await mcp_server.execute_command("echo ok")

    assert result["stdout"] == "ok"
    assert captured["PATH"] == "safe-path"
    assert "TRPC_AGENT_API_KEY" not in captured
