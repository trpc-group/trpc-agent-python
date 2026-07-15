"""Tests for the Python AST scanner."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._guard import ToolSafetyGuard
from trpc_agent_sdk.tools.safety._models import SafetyDecision, SafetyScanRequest, ScriptLanguage
from trpc_agent_sdk.tools.safety._policy import load_safety_policy_dict


@pytest.fixture
def guard(strict_policy_dict):
    return ToolSafetyGuard(load_safety_policy_dict(strict_policy_dict))


def scan(guard, script):
    return guard.scan(SafetyScanRequest(
        tool_name="t", language=ScriptLanguage.PYTHON, script=script,
    ))


def test_recursive_delete_denies(guard):
    report = scan(guard, "import shutil\nshutil.rmtree('/tmp/x')\n")
    assert report.decision == SafetyDecision.DENY
    assert "FILE001_RECURSIVE_DELETE" in report.rule_ids


def test_credential_read_denies(guard):
    script = "open('/home/u/.ssh/id_rsa').read()\n"
    report = scan(guard, script)
    assert report.decision == SafetyDecision.DENY
    assert "FILE003_CREDENTIAL_READ" in report.rule_ids


def test_pathlib_credential_read_denies(guard):
    script = (
        "from pathlib import Path\n"
        "path = Path('/home/u') / '.ssh' / 'id_rsa'\n"
        "print(path.read_text())\n"
    )
    report = scan(guard, script)
    assert "FILE003_CREDENTIAL_READ" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_dotenv_read_denies(guard):
    report = scan(guard, "open('.env').read()\n")
    assert "FILE004_DOTENV_READ" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_non_allowlist_network_denies(guard):
    script = "import requests\nrequests.get('https://evil.example.com')\n"
    report = scan(guard, script)
    assert "NET001_DOMAIN_NOT_ALLOWED" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_allowlist_network_allows(guard):
    script = "import requests\nrequests.get('https://api.github.com/x')\n"
    report = scan(guard, script)
    assert report.decision == SafetyDecision.ALLOW


def test_ip_literal_policy_can_block_an_allowlisted_ip(strict_policy_dict):
    policy_dict = strict_policy_dict.copy()
    policy_dict["network"] = {
        "allow_domains": ["127.0.0.1"],
        "deny_ip_literals": True,
    }
    blocked_guard = ToolSafetyGuard(load_safety_policy_dict(policy_dict))
    script = "import requests\nrequests.get('http://127.0.0.1:8080')\n"
    blocked = scan(blocked_guard, script)
    assert "NET003_IP_LITERAL" in blocked.rule_ids
    assert blocked.decision == SafetyDecision.DENY

    policy_dict["network"] = {
        "allow_domains": ["127.0.0.1"],
        "deny_ip_literals": False,
    }
    allowed_guard = ToolSafetyGuard(load_safety_policy_dict(policy_dict))
    assert scan(allowed_guard, script).decision == SafetyDecision.ALLOW


def test_fstring_allowlist_network_allows(guard):
    script = "import requests\nrequests.get(f'https://api.github.com/users/{name}')\n"
    report = scan(guard, script)
    assert report.decision == SafetyDecision.ALLOW


def test_shell_injection_denies(guard):
    script = "import subprocess\nsubprocess.run('ls; rm -rf /', shell=True)\n"
    report = scan(guard, script)
    assert "PROC002_SHELL_INJECTION" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_allowed_subprocess_allows(guard):
    script = "import subprocess\nsubprocess.run(['python', '-V'])\n"
    report = scan(guard, script)
    assert report.decision == SafetyDecision.ALLOW


def test_pip_install_denies(guard):
    script = "import subprocess\nsubprocess.run(['pip', 'install', 'numpy'])\n"
    report = scan(guard, script)
    assert "DEP001_ENV_MUTATION" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_python_m_pip_install_denies(guard):
    script = "import subprocess\nsubprocess.run(['python', '-m', 'pip', 'install', 'numpy'])\n"
    report = scan(guard, script)
    assert "DEP001_ENV_MUTATION" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_unbounded_while_true_denies(guard):
    report = scan(guard, "while True:\n    pass\n")
    assert "RES001_UNBOUNDED_LOOP" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_long_sleep_denies(guard):
    report = scan(guard, "import time\ntime.sleep(60)\n")
    assert "RES003_LONG_SLEEP" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_process_limit_uses_max_processes(strict_policy_dict):
    policy_dict = strict_policy_dict.copy()
    policy_dict["limits"] = dict(policy_dict["limits"])
    policy_dict["limits"]["max_parallel_tasks"] = 10
    policy_dict["limits"]["max_processes"] = 2
    g = ToolSafetyGuard(load_safety_policy_dict(policy_dict))
    report = scan(
        g,
        "import multiprocessing\nmultiprocessing.Pool(processes=3)\n",
    )
    assert "RES004_CONCURRENCY" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_secret_to_print_denies(guard):
    script = "import os\nv=os.environ['API_TOKEN']\nprint(v)\n"
    report = scan(guard, script)
    assert "SECRET001_LOG_SINK" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_literal_api_key_to_print_denies_and_redacts(guard):
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    report = scan(guard, f"print('{secret}')\n")
    assert "SECRET001_LOG_SINK" in report.rule_ids
    assert report.decision == SafetyDecision.DENY
    assert report.redacted is True
    assert secret not in report.model_dump_json()


def test_secret_to_log_denies(guard):
    script = (
        "import os, logging\n"
        "logging.basicConfig()\n"
        "log = logging.getLogger('x')\n"
        "secret = os.environ['PASSWORD']\n"
        "log.info('pwd=%s', secret)\n"
    )
    report = scan(guard, script)
    assert "SECRET001_LOG_SINK" in report.rule_ids


def test_dynamic_exec_eval_denies(guard):
    report = scan(guard, "eval('1+1')\n")
    assert "OBF001_DYNAMIC_EXEC" in report.rule_ids
    assert report.decision != SafetyDecision.ALLOW


def test_dynamic_command_review(guard):
    script = (
        "import subprocess\n"
        "cmd = input()\n"
        "subprocess.run(cmd)\n"
    )
    report = scan(guard, script)
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW
    assert "PROC001_PROCESS_EXEC" in report.rule_ids


def test_safe_script_allows(guard):
    report = scan(guard, "import os\nprint(os.getcwd())\n")
    assert report.decision == SafetyDecision.ALLOW


def test_syntax_error_yields_review(guard):
    report = scan(guard, "def (\n")
    assert "PARSE001_UNCERTAIN" in report.rule_ids
    assert report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW


def test_script_too_large_fail_closed(strict_policy_dict):
    strict = strict_policy_dict.copy()
    strict["limits"] = dict(strict["limits"])
    strict["limits"]["max_script_bytes"] = 16
    g = ToolSafetyGuard(load_safety_policy_dict(strict))
    report = scan(g, "a" * 200)
    assert report.decision == SafetyDecision.DENY
    assert "GUARD001_INTERNAL_ERROR" in report.rule_ids


def test_privilege_escalation_denies(guard):
    script = "import subprocess\nsubprocess.run(['sudo', 'ls'])\n"
    report = scan(guard, script)
    assert "PROC004_PRIVILEGE" in report.rule_ids
    assert report.decision == SafetyDecision.DENY


def test_rule_override_changes_decision(strict_policy_dict):
    policy_dict = strict_policy_dict.copy()
    policy_dict["rule_overrides"] = {"DEP001_ENV_MUTATION": "allow"}
    g = ToolSafetyGuard(load_safety_policy_dict(policy_dict))
    script = "import subprocess\nsubprocess.run(['pip', 'install', 'numpy'])\n"
    report = scan(g, script)
    # PROC001 may still flag because pip isn't in allow list, but the
    # dependency rule should not be present.
    assert "DEP001_ENV_MUTATION" not in report.rule_ids
