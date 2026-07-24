# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
from __future__ import annotations

from trpc_agent_sdk.tools.safety._policy import load_policy
from trpc_agent_sdk.tools.safety._python_scanner import scan_python


def _scan(src):
    return {f.rule_id for f in scan_python(load_policy(), src)}


def test_safe_code_clean():
    assert _scan("x = 1 + 2\nprint(x)\n") == set()


def test_eval():
    assert "tool-code-unsafe-eval" in _scan("eval(input())")


def test_exec():
    assert "tool-code-unsafe-exec" in _scan("exec('os.system(\"ls\")')")


def test_shutil_rmtree():
    assert "tool-fs-recursive-delete" in _scan("import shutil\nshutil.rmtree('/etc')")


def test_alias_bypass_caught():
    # `import os as x; x.system(...)` must resolve to os.system.
    # os.system is in _DANGEROUS_ATTR -> fires tool-proc-subprocess exactly.
    src = "import os as x\nx.system('rm -rf /')"
    assert "tool-proc-subprocess" in _scan(src)


def test_subprocess():
    assert "tool-proc-subprocess" in _scan("import subprocess\nsubprocess.run(['rm'])")


def test_read_env_credentials():
    src = "open('/root/.env')\nopen('/home/u/.ssh/id_rsa')\n"
    found = [f for f in scan_python(load_policy(), src)
             if f.rule_id == "tool-fs-read-credentials"]
    # Both .env and .ssh/id_rsa paths should fire the credential rule.
    assert len(found) >= 2, f"Expected >=2 credential findings, got {len(found)}"


def test_requests_non_whitelisted():
    assert "tool-net-http" in _scan("import requests\nrequests.get('http://evil.example.org')")


def test_requests_whitelisted_ok():
    assert "tool-net-http" not in _scan("import requests\nrequests.get('https://pypi.org/x')")


def test_infinite_loop():
    assert "tool-res-infinite-loop" in _scan("while True:\n    pass\n")


def test_secret_logging():
    src = 'api_key = "sk-xxxxxx"\nprint(api_key)\n'
    assert "tool-secret-logging" in _scan(src)


def test_syntax_error_falls_back():
    # Malformed python must not raise; scanner degrades gracefully.
    assert isinstance(_scan("def (: "), set)
