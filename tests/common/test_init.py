# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.common public API surface.

Verifies that the package re-exports the expected symbols from _compatible.
"""

from __future__ import annotations

import trpc_agent_sdk.common as common_mod
from trpc_agent_sdk.common import OS_DETECTOR, OSDetector, check_enum
from trpc_agent_sdk.common._compatible import (
    OS_DETECTOR as _ORIG_OS_DETECTOR,
    OSDetector as _OrigOSDetector,
    check_enum as _orig_check_enum,
)


class TestPublicExports:
    """Ensure __init__.py re-exports the right objects."""

    def test_all_contains_expected_names(self):
        assert set(common_mod.__all__) == {"OSDetector", "OS_DETECTOR", "check_enum"}

    def test_os_detector_class_is_same_object(self):
        assert OSDetector is _OrigOSDetector

    def test_os_detector_instance_is_same_object(self):
        assert OS_DETECTOR is _ORIG_OS_DETECTOR

    def test_checkenum_is_same_function(self):
        assert check_enum is _orig_check_enum
