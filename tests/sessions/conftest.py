#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Collection policy for the replay acceptance tests."""

import os

import pytest

BACKEND_MODE_ENVIRONMENT_VARIABLE = "TRPC_REPLAY_BACKENDS"
IN_MEMORY_BACKEND_MODE = "in_memory"
IN_MEMORY_TEST_NAME = "test_in_memory_only_lightweight_mode"
IN_MEMORY_REAL_AGENT_SAFE_TEST_NAME = "test_subtle_realistic_drift_is_detected"
REPLAY_TEST_FILES = frozenset({
    "test_replay_consistency.py",
    "test_replay_real_agent.py",
})
IN_MEMORY_SKIP_REASON = "disabled by TRPC_REPLAY_BACKENDS=in_memory"


def pytest_collection_modifyitems(items):
    """Keep only the lightweight replay when InMemory-only mode is selected."""
    if os.getenv(BACKEND_MODE_ENVIRONMENT_VARIABLE) != IN_MEMORY_BACKEND_MODE:
        return
    skip = pytest.mark.skip(reason=IN_MEMORY_SKIP_REASON)
    for item in items:
        if (item.path.name == "test_replay_consistency.py" and item.name != IN_MEMORY_TEST_NAME) or (
                item.path.name == "test_replay_real_agent.py"
                and item.name != IN_MEMORY_REAL_AGENT_SAFE_TEST_NAME):
            item.add_marker(skip)
