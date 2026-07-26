# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared fixtures for the replay consistency test suite.

The fixture is deliberately tiny: it just provides a per-test work directory
under ``.replay-work`` so SQLite files do not leak between tests. The replay
harness itself closes every backend it opens, so we do not need any global
state.

Integration (real Redis / MySQL) tests coordinate their skip behaviour through
the :func:`integration_runtime` fixture so a missing ``TRPC_REPLAY_REDIS_URL``
or ``TRPC_REPLAY_SQL_URL`` automatically turns the test into a clean skip
without ever touching the optional dependency.
"""
from __future__ import annotations

import importlib
import shutil
from pathlib import Path
from typing import Mapping

import pytest


@pytest.fixture
def replay_work_dir(tmp_path: Path) -> Path:
    """A clean per-test work directory for the replay harness."""
    target = tmp_path / "replay"
    target.mkdir(parents=True, exist_ok=True)
    yield target
    shutil.rmtree(target, ignore_errors=True)


def _import_or_none(module_name: str):
    """Try to import an optional dependency; return ``None`` if unavailable."""
    try:
        return importlib.import_module(module_name)
    except Exception:  # pylint: disable=broad-except
        return None


@pytest.fixture(scope="session")
def integration_runtime() -> Mapping[str, object]:
    """Probe the runtime for opt-in integration backends.

    Returns a mapping with three keys:

    * ``redis_url`` — the value of ``TRPC_REPLAY_REDIS_URL`` if it is set and
      the ``redis`` Python client is importable, otherwise ``None``.
    * ``sql_url`` — the value of ``TRPC_REPLAY_SQL_URL`` if set, otherwise
      ``None``.
    * ``skip_reason`` — a human-readable reason explaining why Redis (or
      SQL) integration tests should be skipped, or ``None`` when both
      backends are available.

    The fixture never raises: missing environment variables or optional
    dependencies are translated into a clear skip reason so test runs on
    contributors' machines without Redis still report a clean ``skipped``.
    """
    import os

    redis_url = os.environ.get("TRPC_REPLAY_REDIS_URL") or None
    sql_url = os.environ.get("TRPC_REPLAY_SQL_URL") or None

    redis_module = _import_or_none("redis")
    redis_asyncio = _import_or_none("redis.asyncio")
    redis_ready = bool(redis_url) and redis_module is not None and redis_asyncio is not None

    skip_reasons = []
    if redis_url and not redis_ready:
        skip_reasons.append(
            "TRPC_REPLAY_REDIS_URL is set but the 'redis' package is not installed; "
            "install with `pip install redis>=6.2.0` to enable Redis integration tests."
        )
    if not redis_url and not sql_url:
        skip_reasons.append(
            "No integration backend configured. Set TRPC_REPLAY_REDIS_URL (or "
            "TRPC_REPLAY_SQL_URL) to opt in to integration replay tests."
        )

    return {
        "redis_url": redis_url if redis_ready else None,
        "sql_url": sql_url,
        "skip_reason": "; ".join(skip_reasons) if skip_reasons else None,
    }