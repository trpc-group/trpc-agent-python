# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import SafetyRiskLevel
from trpc_agent_sdk.tools.safety import ToolSafetyAuditLogger
from trpc_agent_sdk.tools.safety import ToolSafetyBlockedError
from trpc_agent_sdk.tools.safety import ToolSafetyFilter
from trpc_agent_sdk.tools.safety import ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyReport
from trpc_agent_sdk.tools.safety import ToolSafetyScanRequest
from trpc_agent_sdk.tools.safety import ToolSafetyScanner
from trpc_agent_sdk.tools.safety import apply_tool_safety_span_attributes
from trpc_agent_sdk.tools.safety import extract_script_from_tool_args
from trpc_agent_sdk.tools.safety import load_tool_safety_policy
from trpc_agent_sdk.tools.safety import _scanner as scanner_module
from trpc_agent_sdk.tools.safety import _types as types_module
from trpc_agent_sdk.tools.safety._guard import command_to_args
from trpc_agent_sdk.tools.safety._guard import infer_language_from_key
from trpc_agent_sdk.tools.safety._types import max_risk_level
from trpc_agent_sdk.tools.safety._types import risk_level_value
from scripts.tool_safety_check import main as tool_safety_check_main


EXAMPLE_DIR = Path(__file__).resolve().parents[3] / "examples" / "tool_safety_guard"
SAMPLES = EXAMPLE_DIR / "samples"


def policy(**kwargs):
    data = {
        "name": "test-policy",
        "allowed_domains": ["api.github.com"],
        "allowed_commands": ["cat", "echo", "grep", "python", "python3", "bash", "ls", "tee"],
        "denied_paths": ["~/.ssh", ".env", "id_rsa", ".aws/credentials"],
        "deny_risk_level": "high",
        "review_risk_level": "medium",
        "block_on_review": True,
        "max_sleep_seconds": 60,
        "max_loop_iterations": 10000,
    }
    data.update(kwargs)
    return ToolSafetyPolicy.from_mapping(data)


def scan_sample(name: str):
    path = SAMPLES / name
    language = "bash" if path.suffix == ".sh" else "python"
    scanner = ToolSafetyScanner(policy())
    return scanner.scan(ToolSafetyScanRequest(script=path.read_text(encoding="utf-8"), language=language))


@pytest.mark.parametrize(
    ("sample", "decision", "rule_id"),
    [
        ("01_safe_python.py", SafetyDecision.ALLOW, None),
        ("02_dangerous_delete.sh", SafetyDecision.DENY, "TSG-FILE-RECURSIVE-DELETE"),
        ("03_read_secret.py", SafetyDecision.DENY, "TSG-FILE-DENIED-PATH"),
        ("04_network_external.py", SafetyDecision.DENY, "TSG-NETWORK-NONALLOWLIST"),
        ("05_network_allowlist.py", SafetyDecision.ALLOW, "TSG-NETWORK-IMPORT"),
        ("06_subprocess_call.py", SafetyDecision.NEEDS_HUMAN_REVIEW, "TSG-PROCESS-SUBPROCESS"),
        ("07_shell_injection.py", SafetyDecision.DENY, "TSG-SHELL-INJECTION"),
        ("08_dependency_install.sh", SafetyDecision.DENY, "TSG-DEPENDENCY-INSTALL"),
        ("09_infinite_loop.py", SafetyDecision.DENY, "TSG-RESOURCE-INFINITE-LOOP"),
        ("10_sensitive_output.py", SafetyDecision.DENY, "TSG-SECRETS-SINK"),
        ("11_bash_pipe.sh", SafetyDecision.NEEDS_HUMAN_REVIEW, "TSG-SHELL-CONTROL"),
        ("12_needs_review.py", SafetyDecision.NEEDS_HUMAN_REVIEW, "TSG-NETWORK-DYNAMIC"),
    ],
)
def test_issue_required_samples(sample, decision, rule_id):
    report = scan_sample(sample)
    assert report.decision == decision
    assert "decision" in report.to_dict()
    if rule_id:
        assert any(finding.rule_id == rule_id for finding in report.findings)
        first = report.findings[0].to_dict()
        assert first["rule_id"]
        assert first["evidence"]
        assert first["recommendation"]
        assert first["risk_level"]


def test_public_samples_emit_structured_reports_and_meet_acceptance_rates():
    high_risk_samples = {
        "02_dangerous_delete.sh",
        "03_read_secret.py",
        "04_network_external.py",
        "07_shell_injection.py",
        "08_dependency_install.sh",
        "09_infinite_loop.py",
        "10_sensitive_output.py",
    }
    safe_samples = {"01_safe_python.py", "05_network_allowlist.py"}
    required_rule_by_sample = {
        "02_dangerous_delete.sh": "TSG-FILE-RECURSIVE-DELETE",
        "03_read_secret.py": "TSG-FILE-DENIED-PATH",
        "04_network_external.py": "TSG-NETWORK-NONALLOWLIST",
    }

    sample_paths = sorted(SAMPLES.iterdir())
    reports = {path.name: scan_sample(path.name) for path in sample_paths}

    assert len(reports) == 12
    for report in reports.values():
        payload = json.loads(report.to_json())
        assert payload["decision"]
        assert payload["risk_level"]
        if payload["decision"] != SafetyDecision.ALLOW.value:
            for finding in payload["findings"]:
                assert finding["rule_id"]
                assert finding["evidence"]
                assert finding["recommendation"]

    detected_high_risk = sum(1 for name in high_risk_samples if reports[name].decision == SafetyDecision.DENY)
    false_positive_safe = sum(1 for name in safe_samples if reports[name].decision != SafetyDecision.ALLOW)
    assert detected_high_risk / len(high_risk_samples) >= 0.9
    assert false_positive_safe / len(safe_samples) <= 0.1

    for sample, rule_id in required_rule_by_sample.items():
        report = reports[sample]
        assert report.decision == SafetyDecision.DENY
        assert any(finding.rule_id == rule_id for finding in report.findings)


def test_cli_scans_public_samples_and_writes_structured_reports(tmp_path, monkeypatch):
    for sample in sorted(SAMPLES.iterdir()):
        report_path = tmp_path / f"{sample.name}.json"
        audit_path = tmp_path / "audit.jsonl"
        language = "bash" if sample.suffix == ".sh" else "python"
        monkeypatch.setattr(
            "sys.argv",
            [
                "tool_safety_check.py",
                str(sample),
                "--language",
                language,
                "--policy",
                str(EXAMPLE_DIR / "tool_safety_policy.yaml"),
                "--report-out",
                str(report_path),
                "--audit-out",
                str(audit_path),
            ],
        )

        exit_code = tool_safety_check_main()

        payload = json.loads(report_path.read_text(encoding="utf-8"))
        assert exit_code in {0, 2}
        assert payload["decision"]
        assert payload["risk_level"]
        if payload["decision"] != SafetyDecision.ALLOW.value:
            assert payload["findings"]
            assert payload["findings"][0]["rule_id"]
            assert payload["findings"][0]["evidence"]
            assert payload["findings"][0]["recommendation"]


def test_denied_path_policy_change_changes_result():
    script = 'open(".env", "r").read()'
    strict = ToolSafetyScanner(policy(denied_paths=[".env"]))
    relaxed = ToolSafetyScanner(policy(denied_paths=["~/.ssh"]))

    assert strict.scan(ToolSafetyScanRequest(script=script, language="python")).decision == SafetyDecision.DENY
    assert relaxed.scan(ToolSafetyScanRequest(script=script, language="python")).decision == SafetyDecision.ALLOW


def test_allowed_domain_policy_change_changes_result():
    script = 'import requests\nrequests.get("https://evil.example.net/collect")'
    strict = ToolSafetyScanner(policy(allowed_domains=["api.github.com"]))
    relaxed = ToolSafetyScanner(policy(allowed_domains=["evil.example.net"]))

    assert strict.scan(ToolSafetyScanRequest(script=script, language="python")).decision == SafetyDecision.DENY
    assert relaxed.scan(ToolSafetyScanRequest(script=script, language="python")).decision == SafetyDecision.ALLOW


def test_allowed_command_policy_change_changes_result():
    script = "custom_tool --flag"
    strict = ToolSafetyScanner(policy(allowed_commands=["echo"]))
    relaxed = ToolSafetyScanner(policy(allowed_commands=["custom_tool"]))

    strict_report = strict.scan(ToolSafetyScanRequest(script="", language="python", command_args=[script]))
    relaxed_report = relaxed.scan(ToolSafetyScanRequest(script="", language="python", command_args=[script]))
    assert strict_report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert relaxed_report.decision == SafetyDecision.ALLOW


@pytest.mark.parametrize(
    ("script", "language", "rule_id", "decision"),
    [
        ("echo data > /etc/app.conf", "bash", "TSG-FILE-SYSTEM-WRITE", SafetyDecision.DENY),
        ("python worker.py &", "bash", "TSG-PROCESS-BACKGROUND", SafetyDecision.NEEDS_HUMAN_REVIEW),
        ("printf '%s\n' a b | xargs -P 0 -n1 echo", "bash", "TSG-RESOURCE-PARALLELISM", SafetyDecision.DENY),
        ("timeout 999 python job.py", "bash", "TSG-RESOURCE-LONG-TIMEOUT", SafetyDecision.NEEDS_HUMAN_REVIEW),
        (
            "import socket\ns = socket.socket()\ns.connect(('evil.example.net', 443))",
            "python",
            "TSG-NETWORK-NONALLOWLIST",
            SafetyDecision.DENY,
        ),
        (
            "import subprocess\nsubprocess.run(['python', 'job.py'], timeout=999, capture_output=True)",
            "python",
            "TSG-RESOURCE-LONG-TIMEOUT",
            SafetyDecision.NEEDS_HUMAN_REVIEW,
        ),
        (
            "import concurrent.futures\nconcurrent.futures.ThreadPoolExecutor(max_workers=999)",
            "python",
            "TSG-RESOURCE-PARALLELISM",
            SafetyDecision.NEEDS_HUMAN_REVIEW,
        ),
    ],
)
def test_additional_required_risk_coverage(script, language, rule_id, decision):
    report = ToolSafetyScanner(policy()).scan(ToolSafetyScanRequest(script=script, language=language))

    assert report.decision == decision
    assert any(finding.rule_id == rule_id for finding in report.findings)


def test_subprocess_output_capture_has_output_size_recommendation():
    script = "import subprocess\nsubprocess.run(['python', 'job.py'], stdout=subprocess.PIPE)"
    report = ToolSafetyScanner(policy()).scan(ToolSafetyScanRequest(script=script, language="python"))

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    finding = next(item for item in report.findings if item.rule_id == "TSG-RESOURCE-OUTPUT-CAPTURE")
    assert "max_output_bytes" in finding.recommendation


@pytest.mark.parametrize(
    ("script", "language", "rule_id"),
    [
        ("print(", "python", "TSG-PY-SYNTAX"),
        ("password = 'abcdefghijklmnopqrstuvwxyz'", "python", "TSG-SECRETS-LITERAL"),
        ("for i in range(100000):\n    pass", "python", "TSG-RESOURCE-LARGE-LOOP"),
        ("import pip", "python", "TSG-DEPENDENCY-INSTALL-API"),
        ("from urllib import request", "python", "TSG-NETWORK-IMPORT"),
        ("import os\nos.remove('/tmp/demo')", "python", "TSG-FILE-DANGEROUS-OP"),
        ("open('/etc/passwd', 'w')", "python", "TSG-FILE-SYSTEM-WRITE"),
        ("from pathlib import Path\nPath('/etc/app.conf').write_text('x')", "python", "TSG-FILE-SYSTEM-WRITE"),
        ("from pathlib import Path\nPath('out.txt').write_text('" + ("x" * 120) + "')", "python", "TSG-RESOURCE-LARGE-WRITE"),
        ("import time\ntime.sleep(99)", "python", "TSG-RESOURCE-LONG-SLEEP"),
        ("import os\nos.fork()", "python", "TSG-RESOURCE-PROCESS-FANOUT"),
        ("import asyncio\nasyncio.gather(" + ", ".join(f't{i}()' for i in range(40)) + ")", "python",
         "TSG-RESOURCE-PARALLELISM"),
        (":(){ :|:& };:", "bash", "TSG-RESOURCE-FORK-BOMB"),
        ("while true; do echo x; done", "bash", "TSG-RESOURCE-INFINITE-LOOP"),
        ("sleep 99", "bash", "TSG-RESOURCE-LONG-SLEEP"),
        ("curl https://evil.example.net", "bash", "TSG-NETWORK-COMMAND"),
        ("systemctl restart demo", "bash", "TSG-SHELL-DANGEROUS-COMMAND"),
        ("cat ~/.ssh/id_rsa", "bash", "TSG-SECRETS-READ"),
        ("dd if=/dev/zero of=big.bin bs=1M count=1024", "bash", "TSG-RESOURCE-LARGE-WRITE"),
        ("echo $API_KEY", "bash", "TSG-SECRETS-SINK"),
        ("tee /etc/app.conf", "bash", "TSG-FILE-SYSTEM-WRITE"),
        ("echo x > ~/.ssh/config", "bash", "TSG-FILE-DENIED-PATH"),
    ],
)
def test_scanner_additional_branches(script, language, rule_id):
    tuned_policy = policy(max_loop_iterations=10, max_sleep_seconds=10, max_literal_write_bytes=10, max_parallel_tasks=5)
    report = ToolSafetyScanner(tuned_policy).scan(ToolSafetyScanRequest(script=script, language=language))

    assert any(finding.rule_id == rule_id for finding in report.findings)


def test_scanner_unknown_language_and_env_secret_branches():
    report = ToolSafetyScanner(policy()).scan(
        ToolSafetyScanRequest(script="noop", language="ruby", env={"API_KEY": "secret-value"}))

    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert any(finding.rule_id == "TSG-LANG-UNKNOWN" for finding in report.findings)
    assert any(finding.rule_id == "TSG-SECRETS-ENV" for finding in report.findings)
    assert report.redacted is True


def test_scanner_command_arg_prefixes_and_duration_helpers():
    scripts = [
        ("", "bash", ["sudo curl https://evil.example.net"], "TSG-NETWORK-COMMAND"),
        ("", "bash", ["uv pip install requests"], "TSG-DEPENDENCY-INSTALL"),
        ("", "bash", ["poetry add requests"], "TSG-DEPENDENCY-INSTALL"),
        ("", "bash", ["parallel -j0 echo {}"], "TSG-RESOURCE-PARALLELISM"),
        ("", "bash", ["xargs --max-procs=999"], "TSG-RESOURCE-PARALLELISM"),
        ("", "bash", ["timeout -k 1s 10m python job.py"], "TSG-RESOURCE-LONG-TIMEOUT"),
    ]
    scanner = ToolSafetyScanner(policy(max_timeout_seconds=30, max_parallel_tasks=8))

    for script, language, command_args, rule_id in scripts:
        report = scanner.scan(ToolSafetyScanRequest(script=script, language=language, command_args=command_args))
        assert any(finding.rule_id == rule_id for finding in report.findings), [
            (finding.rule_id, finding.evidence) for finding in report.findings
        ]


def test_scanner_edge_branches_from_direct_scans(monkeypatch):
    scanner = ToolSafetyScanner(policy(max_parallel_tasks=2, max_loop_iterations=3))

    assert scanner.scan(ToolSafetyScanRequest(script="", language="python", env=[])).decision == SafetyDecision.ALLOW
    assert scanner._scan_bash_line("", 1) == []  # pylint: disable=protected-access

    monkeypatch.setattr(scanner_module, "split_shell_commands", lambda tokens: [[]])
    assert scanner._scan_bash_line("echo ok", 1) == []  # pylint: disable=protected-access

    scripts = [
        ("funcs = [print]\nfuncs[0]('x')", None),
        ("import os\nos.remove('.env')", "TSG-FILE-DENIED-PATH"),
        ("open('/etc/passwd', mode='w')", "TSG-FILE-SYSTEM-WRITE"),
        ("f = open('out.txt')", None),
        ("import concurrent.futures\nconcurrent.futures.ThreadPoolExecutor()", "TSG-RESOURCE-PARALLELISM"),
        ("for i in range(limit):\n    pass", None),
        ("for i in range(2, 10, 2):\n    pass", "TSG-RESOURCE-LARGE-LOOP"),
    ]
    for script, rule_id in scripts:
        report = scanner.scan(ToolSafetyScanRequest(script=script, language="python"))
        if rule_id:
            assert any(finding.rule_id == rule_id for finding in report.findings)


def test_python_analyzer_helper_edge_branches():
    analyzer = scanner_module._PythonAnalyzer("pass\n", policy())  # pylint: disable=protected-access

    assert analyzer.network_target_from_arg(ast.parse("url", mode="eval").body) == ""
    assert analyzer.node_line(ast.Pass(lineno=1, col_offset=0)) == "pass"
    assert analyzer.node_line(ast.Pass(lineno=99, col_offset=0)) == ""
    assert analyzer.call_name(ast.Attribute(value=ast.Constant(value=1), attr="field", ctx=ast.Load())) == "field"
    assert analyzer.call_name(ast.Constant(value=None)) == ""
    assert analyzer.name_of(ast.Attribute(value=ast.Name(id="obj", ctx=ast.Load()), attr="token", ctx=ast.Load())) == "token"
    assert analyzer.name_of(ast.Constant(value=None)) == ""
    assert analyzer.constant_string(None) == ""
    assert analyzer.constant_string(ast.parse("f'key={value}'", mode="eval").body) == "key={}"
    assert analyzer.constant_string(ast.parse("['a', value]", mode="eval").body) == ""
    assert analyzer.constant_string(ast.parse("('a', 'b')", mode="eval").body) == "a b"
    assert analyzer.path_literal_from_node(None) == ""
    assert analyzer.path_literal_from_node(ast.parse("Path('.env')", mode="eval").body) == ".env"
    assert analyzer.path_literal_from_node(ast.parse("Path(value)", mode="eval").body) == ""
    assert analyzer.path_literal_from_node(ast.parse("Path('.env').expanduser().read_text", mode="eval").body) == ".env"
    assert analyzer.path_literal_from_node(ast.parse("config.read_text", mode="eval").body) == ""
    assert analyzer.range_count(ast.parse("range(limit)", mode="eval").body) is None
    assert analyzer.range_count(ast.parse("range(2, 10, 2)", mode="eval").body) == 4


def test_scanner_module_helper_edge_branches():
    assert scanner_module.is_secret_name("") is False
    assert scanner_module.numeric_constant(ast.parse("value", mode="eval").body) is None
    assert scanner_module.is_install_invocation(["python", "-m", "pip", "install", "demo"]) is False
    assert scanner_module.is_recursive_delete(["echo", "-rf"]) is False
    assert scanner_module.redirection_targets(["echo", "x", ">/etc/app.conf"]) == ["/etc/app.conf"]
    assert scanner_module.timeout_seconds_from_tokens(["timeout", "--kill-after=1s", "10m"]) == 600
    assert scanner_module.timeout_seconds_from_tokens(["timeout", "--preserve-status", "10m"]) == 600
    assert scanner_module.timeout_seconds_from_tokens(["timeout", "--preserve-status"]) is None
    assert scanner_module.duration_token_seconds("never") is None
    assert scanner_module.parallel_tasks_from_tokens(["parallel", "-j", "4"]) == 4
    assert scanner_module.parallel_tasks_from_tokens(["parallel", "--jobs=7"]) == 7
    assert scanner_module.parallel_tasks_from_tokens(["parallel", "echo"]) == 0
    assert scanner_module.parallel_tasks_from_tokens(["xargs", "-P3"]) == 3
    assert scanner_module.int_token("many") is None
    assert scanner_module.line_has_allowlisted_url("echo no-url", policy()) is False


def test_policy_loader_and_matching_branches(tmp_path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        """
policy:
  name: nested
  allowed_domains:
    - "*.example.com"
  allowed_commands:
    - custom-tool
  denied_paths:
    - ""
    - .env
  audit_log_path: audit.jsonl
extra_flag: yes
""",
        encoding="utf-8",
    )

    loaded = ToolSafetyPolicy.load(policy_file)

    assert loaded.name == "nested"
    assert loaded.metadata == {"extra_flag": True}
    assert loaded.audit_log_path == "audit.jsonl"
    assert loaded.is_domain_allowed("api.example.com") is True
    assert loaded.is_domain_allowed("example.com") is True
    assert loaded.is_domain_allowed(None) is False
    assert loaded.is_command_allowed("custom-tool --flag") is True
    assert loaded.is_command_allowed(None) is False
    assert loaded.is_denied_path(None) is False
    assert loaded.is_denied_path("config.yaml") is False
    assert loaded.is_system_write_path("/") is True
    assert loaded.is_system_write_path(None) is False
    assert ToolSafetyPolicy(allowed_domains=[""]).is_domain_allowed("api.example.com") is False
    assert ToolSafetyPolicy(system_write_paths=[""]).is_system_write_path("/etc/passwd") is False
    assert load_tool_safety_policy(None).name == "default"


def test_policy_load_rejects_non_mapping(tmp_path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text("- not-a-mapping\n", encoding="utf-8")

    with pytest.raises(ValueError):
        ToolSafetyPolicy.load(policy_file)


def test_report_and_type_helpers_cover_empty_and_fallback_paths():
    assert max_risk_level([]) == SafetyRiskLevel.NONE
    assert risk_level_value("unknown") == 0

    report = ToolSafetyReport(
        decision="allow",
        risk_level="none",
        findings=[],
        duration_ms=1.2345,
        language="python",
        scanned_at="2026-07-05T00:00:00+00:00",
    )

    assert report.is_allowed is True
    assert json.loads(report.to_json(indent=None))["risk_count"] == 0


def test_max_risk_level_fallback_when_order_has_no_match(monkeypatch):
    monkeypatch.setattr(types_module, "RISK_LEVEL_ORDER", {})

    assert types_module.max_risk_level(["unexpected"]) == SafetyRiskLevel.NONE


def test_audit_event_contains_required_fields(tmp_path):
    report = ToolSafetyScanner(policy()).scan(
        ToolSafetyScanRequest(script="rm -rf /tmp/demo", language="bash", tool_metadata={"name": "Bash"}))
    event = ToolSafetyAuditLogger(tmp_path / "audit.jsonl").write(report)

    assert event["tool_name"] == "Bash"
    assert event["decision"] == "deny"
    assert event["risk_level"] == "high"
    assert event["rule_id"]
    assert event["duration_ms"] >= 0
    assert event["redacted"] is False
    assert event["blocked"] is True


def test_report_includes_telemetry_attributes():
    report = ToolSafetyScanner(policy()).scan(ToolSafetyScanRequest(script="while True:\n    pass", language="python"))

    assert report.telemetry_attributes["tool.safety.decision"] == "deny"
    assert report.telemetry_attributes["tool.safety.risk_level"] == "high"
    assert report.telemetry_attributes["tool.safety.rule_id"] == "TSG-RESOURCE-INFINITE-LOOP"


def test_telemetry_handles_list_values_and_span_errors(monkeypatch):
    class Span:
        def __init__(self):
            self.attributes = {}

        def set_attribute(self, key, value):
            self.attributes[key] = value

    span = Span()
    report = ToolSafetyReport(
        decision=SafetyDecision.ALLOW,
        risk_level=SafetyRiskLevel.LOW,
        findings=[],
        duration_ms=0,
        language="python",
        scanned_at="2026-07-05T00:00:00+00:00",
        telemetry_attributes={
            "string": "value",
            "list": ["a", "b"],
            "object": {"x": 1},
        },
    )

    monkeypatch.setattr("trpc_agent_sdk.tools.safety._telemetry.trace.get_current_span", lambda: span)
    apply_tool_safety_span_attributes(report)
    assert span.attributes["list"] == "a,b"
    assert span.attributes["object"] == "{'x': 1}"

    class RaisingSpan:
        def set_attribute(self, key, value):
            raise RuntimeError("boom")

    monkeypatch.setattr("trpc_agent_sdk.tools.safety._telemetry.trace.get_current_span", lambda: RaisingSpan())
    apply_tool_safety_span_attributes(report)


@pytest.mark.asyncio
async def test_guard_check_and_run_if_allowed_paths(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    guard = ToolSafetyGuard(policy=policy(audit_log_path=str(audit_path)), apply_telemetry=False)

    report = guard.check(ToolSafetyScanRequest(script="print('ok')", language="python"))
    assert report.is_allowed
    assert audit_path.exists()

    result = await guard.run_if_allowed(
        ToolSafetyScanRequest(script="print('ok')", language="python"),
        lambda: AsyncMock(return_value="ran")(),
    )
    assert result == "ran"

    with pytest.raises(ToolSafetyBlockedError):
        guard.check(ToolSafetyScanRequest(script="rm -rf /tmp/demo", language="bash"))


@pytest.mark.asyncio
async def test_tool_filter_blocks_before_handle():
    filter_ = ToolSafetyFilter(guard=ToolSafetyGuard(policy=policy()))
    handle = AsyncMock(return_value={"success": True})

    result = await filter_.run(create_agent_context(), {"command": "rm -rf /tmp/demo"}, handle)

    assert result.is_continue is False
    assert result.rsp["error"] == "TOOL_SAFETY_GUARD_BLOCKED"
    handle.assert_not_called()


@pytest.mark.asyncio
async def test_tool_filter_block_writes_audit_event(tmp_path):
    audit_path = tmp_path / "tool_safety_audit.jsonl"
    filter_ = ToolSafetyFilter(guard=ToolSafetyGuard(policy=policy(), audit_log_path=audit_path))
    handle = AsyncMock(return_value={"success": True})

    result = await filter_.run(create_agent_context(), {"command": "rm -rf /tmp/demo"}, handle)

    assert result.is_continue is False
    handle.assert_not_called()
    event = json.loads(audit_path.read_text(encoding="utf-8").strip())
    assert event["decision"] == "deny"
    assert event["rule_id"] == "TSG-FILE-RECURSIVE-DELETE"
    assert event["blocked"] is True


@pytest.mark.asyncio
async def test_tool_filter_policy_path_and_passthrough_branches(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text("allowed_commands:\n  - echo\n", encoding="utf-8")
    filter_ = ToolSafetyFilter(policy_path=policy_path)

    handle_result = FilterResult(rsp={"ok": True})
    result = await filter_.run(create_agent_context(), {"note": "not script"}, AsyncMock(return_value=handle_result))
    assert result is handle_result

    result = await filter_.run(create_agent_context(), "not mapping", AsyncMock(return_value={"plain": True}))
    assert result.rsp == {"plain": True}


@pytest.mark.asyncio
async def test_tool_filter_allows_safe_command():
    filter_ = ToolSafetyFilter(guard=ToolSafetyGuard(policy=policy()))
    handle = AsyncMock(return_value={"success": True})

    result = await filter_.run(create_agent_context(), {"command": "echo hello"}, handle)

    assert result.rsp == {"success": True}
    handle.assert_awaited_once()


@pytest.mark.asyncio
async def test_safety_guarded_code_executor_blocks_delegate():
    class DelegateExecutor(BaseCodeExecutor):
        called: bool = False

        async def execute_code(self, invocation_context, code_execution_input):
            self.called = True
            return create_code_execution_result(stdout="executed")

    delegate = DelegateExecutor()
    executor = SafetyGuardedCodeExecutor(delegate=delegate, guard=ToolSafetyGuard(policy=policy()))

    result = await executor.execute_code(
        invocation_context=AsyncMock(),
        code_execution_input=CodeExecutionInput(code_blocks=[CodeBlock(code="while True:\n    pass", language="python")]),
    )

    assert "Tool safety guard blocked" in result.output
    assert delegate.called is False


@pytest.mark.asyncio
async def test_safety_guarded_code_executor_code_string_paths():
    class DelegateExecutor(BaseCodeExecutor):
        called: bool = False
        work_dir: str = "/workspace"

        async def execute_code(self, invocation_context, code_execution_input):
            self.called = True
            return create_code_execution_result(stdout="executed")

    delegate = DelegateExecutor()
    executor = SafetyGuardedCodeExecutor(delegate=delegate, guard=ToolSafetyGuard(policy=policy()))

    blocked = await executor.execute_code(
        invocation_context=AsyncMock(),
        code_execution_input=CodeExecutionInput(code="while True:\n    pass"),
    )
    assert "Tool safety guard blocked" in blocked.output
    assert delegate.called is False

    allowed = await executor.execute_code(
        invocation_context=AsyncMock(),
        code_execution_input=CodeExecutionInput(code="print('ok')"),
    )
    assert "executed" in allowed.output
    assert delegate.called is True


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"command": "echo 'unterminated", "cwd": "/tmp"}, ("echo 'unterminated", "bash", "/tmp", ["echo", "'unterminated"])),
        ({"script": "#!/bin/bash\necho ok"}, ("#!/bin/bash\necho ok", "bash", None, [])),
        ({"script": "set -e\necho ok"}, ("set -e\necho ok", "bash", None, [])),
        ({"code": "print('ok')", "lang": "py"}, ("print('ok')", "python", None, [])),
        ({"code_blocks": [{"code": "echo ok", "language": "sh"}]}, ("echo ok", "bash", None, [])),
        ({"code_blocks": [{"language": "sh"}]}, None),
    ],
)
def test_extract_script_from_tool_args_branches(args, expected):
    assert extract_script_from_tool_args(args) == expected


def test_infer_language_from_key_default():
    assert infer_language_from_key("source", "anything") == "python"
    assert command_to_args("echo ok") == ["echo", "ok"]


def test_scans_500_line_script_under_one_second():
    script = "\n".join(f'print("line {i}")' for i in range(500))
    scanner = ToolSafetyScanner(policy())
    start = time.perf_counter()
    report = scanner.scan(ToolSafetyScanRequest(script=script, language="python"))
    elapsed = time.perf_counter() - start

    assert report.decision == SafetyDecision.ALLOW
    assert elapsed < 1.0
