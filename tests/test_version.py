# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

"""Test the version module."""

import pytest
from trpc_agent_sdk.version import __version__

def test_version():
    """Test the version module."""
    assert __version__ == '0.6.2'
