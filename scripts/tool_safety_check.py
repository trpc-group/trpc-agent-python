#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Static safety scanner command; it never executes the supplied source."""

from trpc_agent_sdk.safety._cli import main

if __name__ == "__main__":
    raise SystemExit(main())
