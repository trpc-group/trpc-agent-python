# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
from __future__ import annotations

from trpc_agent_sdk.tools.safety._shell_parse import first_command
from trpc_agent_sdk.tools.safety._shell_parse import has_background
from trpc_agent_sdk.tools.safety._shell_parse import has_pipeline
from trpc_agent_sdk.tools.safety._shell_parse import has_redirection
from trpc_agent_sdk.tools.safety._shell_parse import has_shell_bypass
from trpc_agent_sdk.tools.safety._shell_parse import split_tokens


def test_pipeline_in_quotes_not_detected():
    assert has_pipeline('echo "a|b"') is False
    assert has_pipeline("ls | grep foo") is True


def test_logical_or_not_pipeline():
    assert has_pipeline("a || b") is False
    assert has_pipeline("a | | b") is True  # Two separate pipes


def test_background_ampersand():
    assert has_background("sleep 100 &") is True
    assert has_background("a && b") is False  # logical and, not background
    assert has_background("a & b") is True  # mid-line single & backgrounds a
    assert has_background('echo "x & y"') is False  # & inside quotes is not background


def test_redirection():
    assert has_redirection("ls > out.txt") is True
    assert has_redirection("echo hi") is False


def test_first_command_strips_path():
    assert first_command("/usr/bin/curl http://x") == "curl"


def test_shell_bypass():
    assert has_shell_bypass("bash -c 'rm -rf /'") is True
    assert has_shell_bypass("echo $(whoami)") is True
    assert has_shell_bypass("echo `id`") is True
    assert has_shell_bypass("ls -la") is False


def test_split_tokens():
    from trpc_agent_sdk.tools.safety._shell_parse import split_tokens

    # Basic tokenization
    assert split_tokens("ls -la") == ["ls", "-la"]
    assert split_tokens("echo hello world") == ["echo", "hello", "world"]

    # Tokenization with quotes
    assert split_tokens('echo "hello world"') == ["echo", "hello world"]
    assert split_tokens("echo 'hello world'") == ["echo", "hello world"]

    # Pipeline
    assert split_tokens("ls | grep foo") == ["ls", "|", "grep", "foo"]

    # Background
    assert split_tokens("sleep 100 &") == ["sleep", "100", "&"]

