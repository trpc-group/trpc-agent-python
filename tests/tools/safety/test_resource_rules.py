# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the resource-abuse rules added to close issue #90 risk class #5."""
from __future__ import annotations

from trpc_agent_sdk.tools.safety._bash_scanner import scan_bash
from trpc_agent_sdk.tools.safety._policy import load_policy
from trpc_agent_sdk.tools.safety._python_scanner import scan_python


def _py(src):
    return {f.rule_id for f in scan_python(load_policy(), src)}


def _bash(src):
    return {f.rule_id for f in scan_bash(load_policy(), src)}


def test_python_large_write_huge_payload():
    src = 'open("big.bin", "wb").write(b"\\x00" * 100000000)'
    assert "tool-res-large-write" in _py(src)


def test_python_write_small_payload_not_flagged():
    # Small write must not trigger the large-write rule (false-positive guard).
    assert "tool-res-large-write" not in _py('open("s.txt","w").write("hi")')


def test_python_concurrent_flood_large_pool():
    src = "from concurrent.futures import ThreadPoolExecutor\nThreadPoolExecutor(max_workers=10000)"
    assert "tool-res-concurrent-flood" in _py(src)


def test_python_small_pool_not_flagged():
    src = "from concurrent.futures import ThreadPoolExecutor\nThreadPoolExecutor(max_workers=4)"
    assert "tool-res-concurrent-flood" not in _py(src)


def test_bash_large_write_dd_gigabytes():
    assert "tool-res-large-write" in _bash("dd if=/dev/zero of=big.bin bs=1G count=10")


def test_bash_large_write_head_c_huge():
    assert "tool-res-large-write" in _bash("head -c 50000000 /dev/urandom > big.bin")
