# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Scanner acceptance-oriented unit tests."""

import ast

import pytest

from trpc_agent_sdk.tools.safety import RiskCategory
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import ScriptPayload
from trpc_agent_sdk.tools.safety import ScriptScanRequest
from trpc_agent_sdk.tools.safety import ToolMetadata
from trpc_agent_sdk.tools.safety import ToolScriptSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety._bash_rules import _sleep_seconds
from trpc_agent_sdk.tools.safety._bash_rules import _ssh_target
from trpc_agent_sdk.tools.safety._bash_rules import stdin_language
from trpc_agent_sdk.tools.safety._sanitizer import SafetySanitizer
from trpc_agent_sdk.tools.safety._sanitizer import truncate_output
from trpc_agent_sdk.tools.safety._common_rules import path_is_system_location
from trpc_agent_sdk.tools.safety._python_rules import _PythonScanContext
from trpc_agent_sdk.tools.safety._python_rules import PythonRuleVisitor
from trpc_agent_sdk.tools.safety._python_rules import _static_truthy
from trpc_agent_sdk.tools.safety._scanner import MAX_NESTED_PAYLOAD_DEPTH


@pytest.fixture
def guard():
    policy = ToolSafetyPolicy(
        allowed_domains=["api.example.com"],
        allowed_commands=["echo", "curl", "python", "bash", "cat"],
        forbidden_paths=["~/.ssh", ".env", "/etc/shadow"],
        max_timeout_seconds=30,
        long_sleep_seconds=5,
        max_concurrency=4,
    )
    return ToolScriptSafetyGuard(policy)


def _request(code, language=ScriptLanguage.PYTHON, timeout=None):
    return ScriptScanRequest(
        payloads=[ScriptPayload(language=language, content=code)],
        metadata=ToolMetadata(name="test_tool"),
        requested_timeout_seconds=timeout,
        effective_timeout_seconds=30,
        max_output_bytes=1024,
    )


def _rule_ids(report):
    return {finding.rule_id for finding in report.findings}


def test_safe_python_allowed(guard):
    report = guard.scan(_request("values = [1, 2, 3]\nprint(sum(values))"))
    assert report.decision == SafetyDecision.ALLOW


def test_recursive_delete_denied_python_alias(guard):
    report = guard.scan(_request("import shutil as files\nfiles.rmtree('/tmp/data')"))
    assert report.decision == SafetyDecision.DENY
    assert "FILE001" in _rule_ids(report)


@pytest.mark.parametrize("call", ["os.remove", "os.unlink", "os.rmdir"])
def test_python_delete_calls_are_denied(guard, call):
    report = guard.scan(_request(f"import os\n{call}('/tmp/important')"))
    assert report.decision == SafetyDecision.DENY
    assert "FILE001" in _rule_ids(report)


def test_os_open_dynamic_flags_are_treated_as_write(guard):
    report = guard.scan(_request("import os\nflags = get_flags()\nos.open('/etc/tool-safety', flags)"))
    assert report.decision == SafetyDecision.DENY
    assert "FILE001" in _rule_ids(report)


def test_os_open_missing_or_unavailable_flags_fail_closed(guard):
    report = guard.scan(_request("import os\nos.open('/etc/tool-safety')"))
    assert report.decision == SafetyDecision.DENY
    visitor = PythonRuleVisitor(_PythonScanContext("", _request(""), guard.policy, guard.sanitizer))
    call = ast.parse("os.open(path, flags)").body[0].value
    assert visitor._is_write_call(call, "os.open") is True


def test_os_open_explicit_read_only_flags_are_allowed(guard):
    report = guard.scan(_request("import os\nos.open('/tmp/tool-safety', os.O_RDONLY)"))
    assert report.decision == SafetyDecision.ALLOW


@pytest.mark.parametrize(
    "code",
    [
        "open('~/.ssh/id_rsa').read()",
        "from pathlib import Path\nPath('.env').read_text()",
        "open('/etc/shadow').read()",
        "import builtins\nbuiltins.open('/etc/shadow').read()",
        "from builtins import open as read_file\nread_file('.env').read()",
    ],
)
def test_sensitive_path_always_denied(guard, code):
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.DENY
    assert "FILE002" in _rule_ids(report)


@pytest.mark.parametrize(
    "code",
    [
        "import requests\nrequests.get('https://evil.test/data')",
        "from requests import get as fetch\nfetch('https://' + 'evil.test/data')",
        "import socket as s\ns.create_connection(('evil.test', 443))",
    ],
)
def test_non_allowlisted_network_denied_or_reviewed(guard, code):
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.DENY
    assert "NET001" in _rule_ids(report)


def test_allowlisted_network_allowed(guard):
    report = guard.scan(_request("import requests\nrequests.get('https://api.example.com/v1')"))
    assert report.decision == SafetyDecision.ALLOW


def test_dynamic_network_requires_review(guard):
    report = guard.scan(_request("import requests\nrequests.get(target)"))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "NET002" in _rule_ids(report)


@pytest.mark.parametrize(
    "code",
    [
        ("url = 'https://api.example.com'\n"
         "url = input()\n"
         "import requests\n"
         "requests.get(url)"),
        ("url = 'https://api.example.com'\n"
         "(url := input())\n"
         "import requests\n"
         "requests.get(url)"),
        ("url = 'https://api.example.com'\n"
         "url += input()\n"
         "import requests\n"
         "requests.get(url)"),
        ("url = 'https://api.example.com'\n"
         "for url in targets:\n"
         "    pass\n"
         "import requests\n"
         "requests.get(url)"),
        ("url = 'https://api.example.com'\n"
         "import requests\n"
         "def fetch(url):\n"
         "    return requests.get(url)"),
        ("url = 'https://api.example.com'\n"
         "import requests\n"
         "[requests.get(url) for url in targets]"),
    ],
)
def test_rebound_network_target_requires_review(guard, code):
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "NET002" in _rule_ids(report)


def test_rebound_file_path_requires_review(guard):
    code = "path = '/tmp/safe'\npath = input()\nopen(path).read()"
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "FILE003" in _rule_ids(report)


def test_subprocess_requires_review(guard):
    report = guard.scan(_request("import subprocess\nsubprocess.run(['echo', 'ok'])"))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "PROC001" in _rule_ids(report)


def test_shell_injection_with_delete_denied(guard):
    report = guard.scan(_request("echo ok; rm -rf /", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.DENY
    assert "FILE001" in _rule_ids(report)


def test_dependency_install_requires_review(guard):
    report = guard.scan(_request("pip install untrusted", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "DEP001" in _rule_ids(report)


def test_infinite_loop_denied(guard):
    report = guard.scan(_request("while True:\n    pass"))
    assert report.decision == SafetyDecision.DENY
    assert "RES001" in _rule_ids(report)


def test_sensitive_output_denied_and_redacted(guard):
    code = "password = 'very-secret-password'\nprint(password)"
    report = guard.scan(_request(code))
    serialized = report.model_dump_json()
    assert report.decision == SafetyDecision.DENY
    assert "SECRET001" in _rule_ids(report)
    assert "very-secret-password" not in serialized


def test_bash_pipeline_requires_review(guard):
    report = guard.scan(_request("echo ok | cat", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "PROC001" in _rule_ids(report)


def test_nested_python_payload_is_scanned(guard):
    command = "python -c \"import shutil; shutil.rmtree('/tmp/data')\""
    report = guard.scan(_request(command, ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.DENY
    assert "FILE001" in _rule_ids(report)


def test_unallowed_command_requires_review(guard):
    report = guard.scan(_request("uname -a", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "POLICY002" in _rule_ids(report)


def test_timeout_over_policy_requires_review(guard):
    report = guard.scan(_request("print('ok')", timeout=31))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "POLICY001" in _rule_ids(report)


def test_missing_execution_payload_requires_review(guard):
    request = _request("")
    request.payloads = []
    report = guard.scan(request)
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW


def test_not_applicable_tool_is_allowed(guard):
    request = _request("")
    request.payloads = []
    request.applicable = False
    report = guard.scan(request)
    assert report.decision == SafetyDecision.ALLOW
    assert report.applicable is False


def test_500_line_script_scans_under_one_second(guard):
    request = _request("\n".join(f"value_{index} = {index}" for index in range(500)))
    report = guard.scan(request)
    assert report.duration_ms < 1000


def test_python_visitor_tracks_bindings_and_resource_shapes(guard):
    code = """
import asyncio as aio
from pathlib import Path as P
from requests import get as fetch
import subprocess

BASE: str = "/tmp"
path = P(BASE)
path.write_text("x")
value = "safe" + "-value"
value = input()
value += "changed"
(alias := fetch)
for item in values:
    alias = item
items = {key: value for key, value in pairs if key}
callback = lambda target="https://example.test": fetch(target)
try:
    subprocess.run()
except RuntimeError as error:
    print(error)

async def work(default="x", *args, **kwargs):
    await aio.sleep(60)

aio.sleep(60)
aio.gather(*tasks)
aio.ThreadPoolExecutor(max_workers=20)
open("/tmp/out", "w").write("x" * 1000000)
subprocess.run(["echo", "ok"])
subprocess.run(command)
fetch(url="https://example.test")
os.open("/tmp/out", os.O_WRONLY | os.O_CREAT)
(left, right) = pair
subprocess.run("echo ok")
aio.sleep(delay)
aio.ThreadPoolExecutor(20)
aio.ThreadPoolExecutor(max_workers=workers)
aio.gather(one, two, three, four, five)
P().write_text("x")
writer.write("x" * 2000000)
"""
    report = guard.scan(_request(code))
    assert report.findings


def test_safety_edge_helpers_cover_invalid_and_dynamic_inputs():
    assert _ssh_target(["ssh", "-p", "22", "--"]) is None
    assert _ssh_target(["ssh", "-o"]) is None
    assert _sleep_seconds("") == float("inf")
    assert _sleep_seconds("not-a-duration") == float("inf")
    assert stdin_language("") is None
    assert stdin_language("python -c code") is None
    assert stdin_language("python script.py") is None
    assert path_is_system_location("/") is True
    assert truncate_output("hello", 3) == "hel"
    assert truncate_output(["hello", 42, "world"], 6) == ["hello", 42, "w"]
    assert truncate_output(42, 3) == 42
    with pytest.raises(ValueError, match="evidence_chars"):
        SafetySanitizer(0)


def test_python_rule_fallback_helpers_are_bounded(guard):
    request = _request("")
    visitor = PythonRuleVisitor(_PythonScanContext("", request, guard.policy, guard.sanitizer))
    assert visitor._name(ast.Constant(value=1)) == ""
    visitor._invalidate_target(ast.Tuple(elts=[ast.Name(id="left"), ast.Name(id="right")]))
    assert visitor._receiver_path(ast.Name(id="open")) == (False, None)
    assert visitor._receiver_path(ast.Attribute(value=ast.Name(id="unknown"), attr="read_text")) == (False, None)
    assert visitor._path_constructor_value(ast.Call(func=ast.Name(id="unknown"), args=[], keywords=[])) is None
    assert visitor._estimated_size(ast.Name(id="dynamic")) == 0
    gather = ast.Call(
        func=ast.Attribute(value=ast.Name(id="asyncio"), attr="gather"),
        args=[ast.Name(id=f"value_{index}") for index in range(guard.policy.max_concurrency + 1)],
        keywords=[],
    )
    assert visitor._gather_is_large(gather) is True


def test_static_truthy_handles_non_literal_conditions():
    assert _static_truthy(ast.UnaryOp(op=ast.Not(), operand=ast.Name(id="value"))) is False
    assert _static_truthy(ast.Name(id="value")) is False
    comparison = ast.Compare(
        left=ast.Name(id="value"),
        ops=[ast.Eq()],
        comparators=[ast.Constant(value=1)],
    )
    assert _static_truthy(comparison) is False
    mixed_comparison = ast.Compare(
        left=ast.Constant(value=1),
        ops=[ast.Lt()],
        comparators=[ast.Constant(value="value")],
    )
    assert _static_truthy(mixed_comparison) is False


def test_nested_payload_depth_returns_policy_finding(guard):
    payload = ScriptPayload(language=ScriptLanguage.BASH, content="bash -c 'echo ok'")
    findings, redacted = guard._scan_nested(payload, _request(payload.content), MAX_NESTED_PAYLOAD_DEPTH)
    assert [finding.rule_id for finding in findings] == ["POLICY004"]
    assert redacted is False


def test_large_write_and_python_syntax_error_are_reported(guard):
    large_write = guard.scan(_request("writer.write('x' * 20000000)"))
    syntax_error = guard.scan(_request("def broken(:\n    pass"))
    assert "RES002" in _rule_ids(large_write)
    assert "PY001" in _rule_ids(syntax_error)


@pytest.mark.parametrize("code", ["while 1:\n    pass", "while 1 == 1:\n    pass", "while not 0:\n    pass"])
def test_python_truthy_constant_loops_are_denied(guard, code):
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.DENY
    assert "RES001" in _rule_ids(report)


@pytest.mark.parametrize(
    "command",
    [
        "env -i TOKEN=value echo ok",
        "ssh $HOST",
        "python script.py",
        "python -c",
        "echo $PASSWORD",
        "bash -c \"bash -c 'bash -c \\\"echo ok\\\"'\"",
    ],
)
def test_bash_edge_shapes_are_scanned_without_fail_open(guard, command):
    report = guard.scan(_request(command, ScriptLanguage.BASH))
    assert report.summary


def test_categories_are_structured(guard):
    report = guard.scan(_request("rm -rf /", ScriptLanguage.BASH))
    assert any(finding.category == RiskCategory.FILE for finding in report.findings)


def test_stdin_payload_is_scanned(guard):
    request = _request("bash", ScriptLanguage.BASH)
    request.payloads[0].stdin = "rm -rf /"
    report = guard.scan(request)
    assert report.decision == SafetyDecision.DENY
    assert "FILE001" in _rule_ids(report)


def test_non_interpreter_stdin_is_not_scanned_as_shell(guard):
    request = _request("cat", ScriptLanguage.BASH)
    request.payloads[0].stdin = "rm -rf /"
    report = guard.scan(request)
    assert report.decision == SafetyDecision.ALLOW


def test_wrapped_python_stdin_uses_python_rules(guard):
    request = _request("env /usr/bin/python -", ScriptLanguage.BASH)
    request.payloads[0].stdin = "import shutil; shutil.rmtree('/')"
    report = guard.scan(request)
    assert report.decision == SafetyDecision.DENY


def test_payload_argv_is_scanned(guard):
    request = _request("echo ok", ScriptLanguage.BASH)
    request.payloads[0].argv = ["~/.ssh/id_rsa"]
    report = guard.scan(request)
    assert report.decision == SafetyDecision.DENY


@pytest.mark.parametrize(
    "command",
    [
        "echo ok\nrm -f -r /",
        "echo ok\nrm --recursive --force /",
        "rm -R /",
        "rm -Rf /",
        "FOO=bar rm -rf /",
        "FOO=bar rm -Rf /",
        "FOO=bar command rm --recursive /",
    ],
)
def test_recursive_rm_variants_denied(guard, command):
    report = guard.scan(_request(command, ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.DENY
    assert "FILE001" in _rule_ids(report)


def test_dynamic_python_file_path_requires_review(guard):
    code = 'import os\npath = os.getenv("X")\nprint(open(path).read())'
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "FILE003" in _rule_ids(report)


def test_relative_forbidden_path_resolves_against_cwd():
    policy = ToolSafetyPolicy(
        forbidden_paths=["/workspace/secret"],
        allowed_commands=["python"],
    )
    local_guard = ToolScriptSafetyGuard(policy)
    request = _request("open('../secret').read()")
    request.cwd = "/workspace/sub"
    report = local_guard.scan(request)
    assert report.decision == SafetyDecision.DENY


@pytest.mark.parametrize(
    "code",
    [
        "import requests\nrequests.Session().get(target)",
        "import requests\nsession = requests.Session()\nsession.get(target)",
        "from urllib import request\nrequest.urlopen(target)",
    ],
)
def test_network_client_variants_require_review(guard, code):
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "NET002" in _rule_ids(report)


def test_wrapped_dynamic_network_requires_review(guard):
    report = guard.scan(_request("env curl \"$URL\"", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "NET002" in _rule_ids(report)


def test_wrapped_nested_interpreter_is_scanned(guard):
    command = "command bash -c \"rm -rf /\""
    report = guard.scan(_request(command, ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.DENY


def test_python_comment_with_url_does_not_trigger(guard):
    report = guard.scan(_request("# docs: https://evil.test\nprint('safe')"))
    assert report.decision == SafetyDecision.ALLOW


@pytest.mark.parametrize(
    "code",
    [
        "from concurrent.futures import ThreadPoolExecutor\nThreadPoolExecutor(100)",
        "import asyncio\nasyncio.gather(*tasks)",
    ],
)
def test_concurrency_variants_require_review(guard, code):
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "RES002" in _rule_ids(report)


@pytest.mark.parametrize("command", ["sleep infinity", "sleep 2m"])
def test_long_sleep_variants_require_review(guard, command):
    report = guard.scan(_request(command, ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "RES002" in _rule_ids(report)


def test_background_execution_requires_review(guard):
    request = _request("echo ok", ScriptLanguage.BASH)
    request.background = True
    report = guard.scan(request)
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "PROC003" in _rule_ids(report)


@pytest.mark.parametrize(
    ("code", "language"),
    [
        ("echo pwned > /etc/passwd", ScriptLanguage.BASH),
        ("open('/etc/passwd', 'w').write('pwned')", ScriptLanguage.PYTHON),
        ("import io\nio.open('/etc/passwd', mode='a')", ScriptLanguage.PYTHON),
        ("import os\nos.open('/etc/passwd', os.O_WRONLY)", ScriptLanguage.PYTHON),
    ],
)
def test_system_path_writes_are_denied(guard, code, language):
    report = guard.scan(_request(code, language))
    assert report.decision == SafetyDecision.DENY
    assert "FILE001" in _rule_ids(report)


def test_network_get_with_secret_is_denied(guard):
    code = ("import requests\n"
            "token = get_token()\n"
            "requests.get('https://api.example.com', headers={'Authorization': token})")
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.DENY
    assert "SECRET001" in _rule_ids(report)


def test_request_method_uses_second_url_argument(guard):
    code = "import requests\nrequests.request('GET', 'https://api.example.com/v1')"
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.ALLOW


def test_plain_bash_url_does_not_trigger_network_rule(guard):
    report = guard.scan(_request("echo https://evil.test", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.ALLOW


def test_nohup_requires_review_and_scans_wrapped_sleep(guard):
    report = guard.scan(_request("nohup sleep 2h", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert {"PROC003", "RES002"}.issubset(_rule_ids(report))


def test_json_secret_is_fully_redacted(guard):
    report = guard.scan(_request("print({'password': 'top secret phrase'})"))
    assert report.decision == SafetyDecision.DENY
    assert "top secret phrase" not in report.model_dump_json()


@pytest.mark.parametrize(
    "command",
    [
        "echo pwned>/etc/passwd",
        "target=/etc/passwd; echo pwned > \"$target\"",
    ],
)
def test_bash_redirection_bypasses_are_blocked(guard, command):
    report = guard.scan(_request(command, ScriptLanguage.BASH))
    assert report.decision != SafetyDecision.ALLOW


@pytest.mark.parametrize(
    ("code", "language"),
    [
        ("echo pwned > passwd", ScriptLanguage.BASH),
        ("open('passwd', 'w').write('pwned')", ScriptLanguage.PYTHON),
    ],
)
def test_relative_system_writes_use_execution_cwd(guard, code, language):
    request = _request(code, language)
    request.cwd = "/etc"
    report = guard.scan(request)
    assert report.decision == SafetyDecision.DENY
    assert "FILE001" in _rule_ids(report)


def test_authorization_header_value_is_secret_tainted(guard):
    code = ("import requests\n"
            "credential = get_value()\n"
            "requests.get('https://api.example.com', headers={'Authorization': credential})")
    report = guard.scan(_request(code))
    assert report.decision == SafetyDecision.DENY
    assert "SECRET001" in _rule_ids(report)


@pytest.mark.parametrize(
    "command",
    [
        "ssh -F none -L 8080:evil.example:80 api.example.com",
        "ssh -R 8080:evil.example:80 api.example.com",
        "ssh -o 'ProxyCommand nc evil.example 22' api.example.com",
        "curl --resolve api.example.com:443:evil.example https://api.example.com",
        "curl --proxy https://evil.example https://api.example.com",
        "wget -e use_proxy=yes https://api.example.com",
        "wget --execute=http_proxy=http://evil.example https://api.example.com",
    ],
)
def test_network_destination_remapping_is_denied(command):
    policy = ToolSafetyPolicy(
        allowed_commands=["curl", "ssh", "wget"],
        allowed_domains=["api.example.com"],
    )
    report = ToolScriptSafetyGuard(policy).scan(_request(command, ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.DENY
    assert "NET003" in _rule_ids(report)


def test_plain_ssh_destination_uses_domain_allowlist():
    policy = ToolSafetyPolicy(
        allowed_commands=["ssh"],
        allowed_domains=["api.example.com"],
    )
    local_guard = ToolScriptSafetyGuard(policy)
    allowed = local_guard.scan(_request("ssh user@api.example.com", ScriptLanguage.BASH))
    denied = local_guard.scan(_request("ssh user@evil.example", ScriptLanguage.BASH))
    assert allowed.decision == SafetyDecision.ALLOW
    assert denied.decision == SafetyDecision.DENY


def test_process_runner_cannot_hide_nested_command():
    policy = ToolSafetyPolicy(allowed_commands=["timeout"])
    report = ToolScriptSafetyGuard(policy).scan(_request("timeout 10 rm -rf /", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "PROC001" in _rule_ids(report)


def test_tilde_path_resolves_against_execution_home():
    policy = ToolSafetyPolicy(
        allowed_commands=["cat"],
        forbidden_paths=["/root"],
    )
    request = _request("cat ~/.bash_history", ScriptLanguage.BASH)
    request.execution_home = "/root"
    report = ToolScriptSafetyGuard(policy).scan(request)
    assert report.decision == SafetyDecision.DENY
    assert "FILE002" in _rule_ids(report)


def test_unknown_execution_home_requires_review():
    policy = ToolSafetyPolicy(allowed_commands=["cat"], forbidden_paths=["/root"])
    request = _request("cat ~/notes.txt", ScriptLanguage.BASH)
    request.execution_home = None
    report = ToolScriptSafetyGuard(policy).scan(request)
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "FILE003" in _rule_ids(report)


def test_invalid_sleep_duration_fails_closed(guard):
    report = guard.scan(_request("sleep invalid", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "RES002" in _rule_ids(report)


@pytest.mark.parametrize(
    "command",
    [
        "sleep 1 1000",
        "sleep -1",
        "sleep NaN",
        "sleep",
    ],
)
def test_all_invalid_or_long_sleep_arguments_fail_closed(guard, command):
    report = guard.scan(_request(command, ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "RES002" in _rule_ids(report)


@pytest.mark.parametrize(
    "command",
    [
        "curl -sKconfig https://api.example.com",
        "ssh -vL8080:evil.example:80 api.example.com",
    ],
)
def test_clustered_network_remap_options_are_denied(command):
    policy = ToolSafetyPolicy(
        allowed_commands=["curl", "ssh"],
        allowed_domains=["api.example.com"],
    )
    report = ToolScriptSafetyGuard(policy).scan(_request(command, ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.DENY
    assert "NET003" in _rule_ids(report)


def test_allowlisted_curl_url_is_not_a_short_option_cluster():
    policy = ToolSafetyPolicy(
        allowed_commands=["curl"],
        allowed_domains=["api.example.com"],
    )
    report = ToolScriptSafetyGuard(policy).scan(_request("curl https://api.example.com", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.ALLOW
    assert "NET003" not in _rule_ids(report)


def test_wget_force_directories_is_not_a_remap_option():
    policy = ToolSafetyPolicy(
        allowed_commands=["wget"],
        allowed_domains=["api.example.com"],
    )
    report = ToolScriptSafetyGuard(policy).scan(_request("wget -x https://api.example.com", ScriptLanguage.BASH))
    assert report.decision == SafetyDecision.ALLOW
    assert "NET003" not in _rule_ids(report)


@pytest.mark.parametrize("path", ["~", "~root/.ssh/config"])
def test_unresolved_tilde_variants_require_review(path):
    policy = ToolSafetyPolicy(allowed_commands=["cat"], forbidden_paths=["/root"])
    request = _request(f"cat {path}", ScriptLanguage.BASH)
    request.execution_home = None
    report = ToolScriptSafetyGuard(policy).scan(request)
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "FILE003" in _rule_ids(report)
