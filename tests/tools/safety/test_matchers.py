# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from __future__ import annotations

from trpc_agent_sdk.tools.safety import SafetyPolicy
from trpc_agent_sdk.tools.safety import get_command_name
from trpc_agent_sdk.tools.safety import is_command_allowed
from trpc_agent_sdk.tools.safety import is_command_denied
from trpc_agent_sdk.tools.safety import is_domain_allowed
from trpc_agent_sdk.tools.safety import is_env_key_sensitive
from trpc_agent_sdk.tools.safety import is_path_denied
from trpc_agent_sdk.tools.safety import matches_any_pattern


class TestDomainMatching:
    """Test allowed domain matching semantics."""

    def test_exact_domain_matches_only_bare_domain(self):
        allowed = ["example.com"]

        assert is_domain_allowed("example.com", allowed)
        assert is_domain_allowed("EXAMPLE.COM.", allowed)
        assert not is_domain_allowed("api.example.com", allowed)

    def test_wildcard_domain_matches_subdomains_not_bare_domain(self):
        allowed = ["*.example.com"]

        assert is_domain_allowed("api.example.com", allowed)
        assert is_domain_allowed("deep.api.example.com", allowed)
        assert not is_domain_allowed("example.com", allowed)
        assert not is_domain_allowed("", allowed)


class TestPatternMatching:
    """Test generic pattern matching."""

    def test_matches_any_pattern_is_case_insensitive(self):
        assert matches_any_pattern("OPENAI_API_KEY", ["*key*"])
        assert matches_any_pattern("Token", ["*TOKEN*"])
        assert not matches_any_pattern("", ["*TOKEN*"])


class TestPathMatching:
    """Test denied path matching semantics."""

    def test_denied_path_matches_exact_descendant_and_wildcard(self):
        policy = SafetyPolicy(denied_paths=[".env", ".env.*", "~/.ssh", "/etc"])

        assert is_path_denied(".env", policy)
        assert is_path_denied("./.env.local", policy)
        assert is_path_denied("~/.ssh/id_rsa", policy)
        assert is_path_denied("/etc/passwd", policy)
        assert not is_path_denied("workspace/.env.sample", policy)

    def test_denied_path_matches_windows_style_patterns(self):
        policy = SafetyPolicy(denied_paths=[r"C:\Users\*\.ssh"])

        assert is_path_denied(r"C:\Users\alice\.ssh\id_rsa", policy)
        assert not is_path_denied(r"C:\Users\alice\Documents\note.txt", policy)


class TestEnvKeyMatching:
    """Test sensitive env key matching."""

    def test_sensitive_env_key_uses_policy_patterns(self):
        policy = SafetyPolicy(sensitive_env_keys=["*KEY*", "AWS_*"])

        assert is_env_key_sensitive("OPENAI_API_KEY", policy)
        assert is_env_key_sensitive("aws_secret_access_key", policy)
        assert not is_env_key_sensitive("SAFE_FLAG", policy)


class TestCommandMatching:
    """Test command allow/deny helpers."""

    def test_get_command_name_handles_strings_lists_paths_and_extensions(self):
        assert get_command_name("python -m pip install package") == "python"
        assert get_command_name(["/usr/bin/git", "status"]) == "git"
        assert get_command_name(r"C:\Python311\python.exe -m pip") == "python"
        assert get_command_name("") == ""

    def test_command_allowed_defaults_true_when_no_allowlist(self):
        policy = SafetyPolicy()

        assert is_command_allowed("anything --flag", policy)

    def test_command_allowlist_and_denylist_match_first_command_token(self):
        policy = SafetyPolicy(allowed_commands=["python", "git"], denied_commands=["sudo", "chmod"])

        assert is_command_allowed("python -m pytest", policy)
        assert is_command_allowed(["/usr/bin/git", "status"], policy)
        assert not is_command_allowed("bash run.sh", policy)
        assert is_command_denied("sudo whoami", policy)
        assert is_command_denied("/bin/chmod 777 file", policy)
        assert not is_command_denied("git status", policy)
