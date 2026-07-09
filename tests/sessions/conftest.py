# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Fixtures and backend factory for replay consistency tests."""

from __future__ import annotations

import os
from pathlib import Path
from typing import AsyncGenerator

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.sessions import SessionServiceConfig


REPLAY_CASES_DIR = Path(__file__).parent / "replay_cases"


def _make_session_config() -> SessionServiceConfig:
    config = SessionServiceConfig()
    config.clean_ttl_config()
    return config


def _make_memory_config() -> MemoryServiceConfig:
    config = MemoryServiceConfig(enabled=True)
    config.clean_ttl_config()
    return config


def pytest_addoption(parser):
    parser.addoption(
        "--run-sql",
        action="store_true",
        default=False,
        help="Run tests with SQL backend (requires SQLite, auto-enabled in CI)",
    )
    parser.addoption(
        "--run-redis",
        action="store_true",
        default=False,
        help="Run tests with Redis backend (requires TRPC_TEST_REDIS_URL env var)",
    )
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run all integration backends (SQL + Redis if available)",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "lightweight: tests that only run with InMemory backend")
    config.addinivalue_line("markers", "sql: tests that require SQL backend")
    config.addinivalue_line("markers", "redis: tests that require Redis backend")


def _should_run_sql(request) -> bool:
    if request.config.getoption("--run-sql"):
        return True
    if request.config.getoption("--run-integration"):
        return True
    if os.environ.get("TRPC_TEST_RUN_SQL", "").lower() in ("1", "true", "yes"):
        return True
    if os.environ.get("CI", "").lower() in ("1", "true", "yes"):
        return True
    return False


def _should_run_redis(request) -> bool:
    if request.config.getoption("--run-redis"):
        return True
    if request.config.getoption("--run-integration"):
        return True
    if os.environ.get("TRPC_TEST_REDIS_URL"):
        return True
    return False


@pytest.fixture
def replay_cases_dir() -> Path:
    return REPLAY_CASES_DIR


@pytest.fixture
async def inmemory_services():
    session_service = InMemorySessionService(session_config=_make_session_config())
    memory_service = InMemoryMemoryService(memory_service_config=_make_memory_config())
    yield session_service, memory_service
    await session_service.close()
    await memory_service.close()


@pytest.fixture
async def sql_services(request):
    if not _should_run_sql(request):
        pytest.skip("SQL backend not enabled (use --run-sql or --run-integration)")
    try:
        from trpc_agent_sdk.memory import SqlMemoryService
        from trpc_agent_sdk.sessions import SqlSessionService
    except ImportError:
        pytest.skip("SQL backend dependencies not available")

    db_url = os.environ.get("TRPC_TEST_SQL_URL", "sqlite:///test_replay_consistency.db")
    session_service = SqlSessionService(db_url=db_url, session_config=_make_session_config(), is_async=False)
    memory_service = SqlMemoryService(db_url=db_url, memory_service_config=_make_memory_config(), is_async=False)
    yield session_service, memory_service
    await session_service.close()
    await memory_service.close()


@pytest.fixture
async def redis_services(request):
    if not _should_run_redis(request):
        pytest.skip("Redis backend not enabled (set TRPC_TEST_REDIS_URL or use --run-redis)")
    try:
        from trpc_agent_sdk.memory import RedisMemoryService
        from trpc_agent_sdk.sessions import RedisSessionService
    except ImportError:
        pytest.skip("Redis backend dependencies not available")

    redis_url = os.environ.get("TRPC_TEST_REDIS_URL", "redis://localhost:6379")
    session_service = RedisSessionService(db_url=redis_url, session_config=_make_session_config())
    memory_service = RedisMemoryService(db_url=redis_url, memory_service_config=_make_memory_config())
    yield session_service, memory_service
    await session_service.close()
    await memory_service.close()


@pytest.fixture
async def all_backends(inmemory_services, sql_services, redis_services, request):
    backends = {
        "inmemory": inmemory_services,
    }

    if _should_run_sql(request):
        backends["sql"] = sql_services

    if _should_run_redis(request):
        backends["redis"] = redis_services

    return backends