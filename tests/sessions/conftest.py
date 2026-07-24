# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Pytest fixtures for replay consistency testing.

每个 fixture 对应一个 replay case，测试类通过 fixture 名称引用。
"""

from __future__ import annotations

import pytest

from .replay_cases import (
    ReplayCase,
    build_single_turn_text,
    build_multi_turn_text,
    build_tool_call_response,
    build_state_basic_update,
    build_state_three_tier,
    build_memory_store_search,
    build_memory_multi_session,
    build_summary_generation,
    build_summary_truncation,
    build_summary_error_detection,
    build_error_duplicate_write,
)


@pytest.fixture(scope="module")
def case_single_turn_text() -> ReplayCase:
    return build_single_turn_text()


@pytest.fixture(scope="module")
def case_multi_turn_text() -> ReplayCase:
    return build_multi_turn_text()


@pytest.fixture(scope="module")
def case_tool_call_response() -> ReplayCase:
    return build_tool_call_response()


@pytest.fixture(scope="module")
def case_state_basic_update() -> ReplayCase:
    return build_state_basic_update()


@pytest.fixture(scope="module")
def case_state_three_tier() -> ReplayCase:
    return build_state_three_tier()


@pytest.fixture(scope="module")
def case_memory_store_search() -> ReplayCase:
    return build_memory_store_search()


@pytest.fixture(scope="module")
def case_memory_multi_session() -> ReplayCase:
    return build_memory_multi_session()


@pytest.fixture(scope="module")
def case_summary_generation() -> ReplayCase:
    return build_summary_generation()


@pytest.fixture(scope="module")
def case_summary_truncation() -> ReplayCase:
    return build_summary_truncation()


@pytest.fixture(scope="module")
def case_summary_error_detection() -> ReplayCase:
    return build_summary_error_detection()


@pytest.fixture(scope="module")
def case_error_duplicate_write() -> ReplayCase:
    return build_error_duplicate_write()
