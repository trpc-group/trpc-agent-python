# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tools for the Advanced Memory SQL session example."""


def large_report(topic: str) -> dict[str, str]:
    """Return a deliberately large result for the compression demo."""
    return {"output": f"Report for {topic}\n" + ("detail " * 2_000)}
