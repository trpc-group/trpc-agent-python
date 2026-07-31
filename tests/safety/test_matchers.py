# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Boundary-aware matcher tests."""

from trpc_agent_sdk.safety._matchers import command_matches
from trpc_agent_sdk.safety._matchers import domain_matches
from trpc_agent_sdk.safety._matchers import normalize_domain
from trpc_agent_sdk.safety._matchers import normalize_path
from trpc_agent_sdk.safety._matchers import path_matches


def test_domain_exact_wildcard_url_ipv6_and_scp_style():
    assert domain_matches("https://api.example.com/x", ["api.example.com"])
    assert domain_matches("deep.service.example.com", ["*.example.com"])
    assert not domain_matches("example.com", ["*.example.com"])
    assert not domain_matches("notexample.com", ["*.example.com"])
    assert normalize_domain("user@host.example:/tmp/x") == "host.example"
    assert normalize_domain("[2001:db8::1]:443") == "2001:db8::1"


def test_paths_are_lexical_component_aware_and_do_not_touch_disk():
    assert normalize_path("./data/../safe.txt", "/workspace") == "/workspace/safe.txt"
    assert path_matches("/etc/passwd", ["/etc"])
    assert not path_matches("/etcetera/file", ["/etc"])
    assert path_matches("~/.ssh/id_rsa", ["/home/<user>/.ssh"])
    assert path_matches("workspace/file", ["/workspace"], cwd="/workspace")


def test_command_matches_basename_or_exact_only():
    assert command_matches("/usr/bin/python3", ["python3"])
    assert command_matches("echo", ["echo"])
    assert not command_matches("echo-danger", ["echo"])
