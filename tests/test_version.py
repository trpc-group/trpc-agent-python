# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Test the version module."""

import pytest
from trpc_agent_sdk.version import __version__

def test_version():
    """Test the version module."""
    assert __version__ == '0.1.0'
