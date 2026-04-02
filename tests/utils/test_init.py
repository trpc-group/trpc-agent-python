# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.utils.__init__ re-exports.

Covers:
- All public symbols are correctly re-exported via __all__
- Each re-exported name resolves to the correct object
"""

import trpc_agent_sdk.utils as utils_pkg
from trpc_agent_sdk.utils._context_utils import AsyncClosingContextManager as _ACM
from trpc_agent_sdk.utils._execute_cmd import CommandExecResult as _CER
from trpc_agent_sdk.utils._execute_cmd import async_execute_command as _aec
from trpc_agent_sdk.utils._hash_key import user_key as _uk
from trpc_agent_sdk.utils._registry_factory import BaseRegistryFactory as _BRF
from trpc_agent_sdk.utils._singleton import SingletonBase as _SB
from trpc_agent_sdk.utils._singleton import SingletonMeta as _SM
from trpc_agent_sdk.utils._singleton import singleton as _sg


class TestAllExports:

    def test_all_contains_expected_names(self):
        expected = {
            "AsyncClosingContextManager",
            "CommandExecResult",
            "async_execute_command",
            "user_key",
            "BaseRegistryFactory",
            "SingletonBase",
            "SingletonMeta",
            "singleton",
        }
        assert set(utils_pkg.__all__) == expected

    def test_reexported_objects_match_originals(self):
        assert utils_pkg.AsyncClosingContextManager is _ACM
        assert utils_pkg.CommandExecResult is _CER
        assert utils_pkg.async_execute_command is _aec
        assert utils_pkg.user_key is _uk
        assert utils_pkg.BaseRegistryFactory is _BRF
        assert utils_pkg.SingletonBase is _SB
        assert utils_pkg.SingletonMeta is _SM
        assert utils_pkg.singleton is _sg
