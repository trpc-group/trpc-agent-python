# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared pytest fixtures for the code review agent tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from trpc_agent_sdk.agents import LlmAgent  # noqa: E402
from trpc_agent_sdk.context import InvocationContext, create_agent_context  # noqa: E402
from trpc_agent_sdk.sessions import InMemorySessionService  # noqa: E402

import review_agent.agent  # noqa: E402,F401  (registers FakeReviewModel)


@pytest.fixture()
def invocation_context():
    """Minimal InvocationContext for driving tools directly."""
    service = InMemorySessionService()
    session = asyncio.run(service.create_session(app_name="cr-test", user_id="u1", session_id="s1"))
    agent = LlmAgent(name="cr_test_agent", model="fake-review-model")
    return InvocationContext(
        session_service=service,
        invocation_id="inv-test",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )


@pytest.fixture()
def fixtures_dir() -> Path:
    return BASE_DIR / "fixtures"


@pytest.fixture(autouse=True)
def _writable_tmp(tmp_path):
    """Staged skill dirs are chmod'ed read-only by the SDK; restore write
    permission afterwards so pytest can garbage-collect old tmp dirs."""
    yield
    import os
    import stat

    for root, dirs, _files in os.walk(tmp_path):
        for name in dirs:
            path = Path(root) / name
            try:
                path.chmod(path.stat().st_mode | stat.S_IWUSR)
            except OSError:
                pass
