"""Benchmark bootstrap and teardown."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from ._config import AgentBenchConf
from ._model import MockOpenAIServer
from ._model import Turn
from ._target import AgentTarget
from ._target import load_target


@dataclass
class BenchSetup:
    target: AgentTarget
    prepared: Any
    conf: AgentBenchConf
    _server: MockOpenAIServer = field(repr=False)


async def setup(
    turns: list[Turn],
    *,
    conf: AgentBenchConf | None = None,
) -> BenchSetup:
    for logger_name in ("trpc_agent_sdk", ):
        logging.getLogger(logger_name).setLevel(logging.WARNING)
    try:
        from trpc_agent_sdk.log import DefaultLogger
        from trpc_agent_sdk.log import LogLevel
        from trpc_agent_sdk.log import set_default_logger

        set_default_logger(DefaultLogger(min_level=LogLevel.WARNING))
    except Exception:
        pass

    conf = conf or AgentBenchConf()
    target = load_target()
    server = MockOpenAIServer(turns, conf.model_conf)
    try:
        conf.base_url = server.start()
        prepared = await target.prepare(conf=conf)
    except Exception:
        conf.base_url = None
        server.stop()
        raise
    return BenchSetup(
        target=target,
        prepared=prepared,
        conf=conf,
        _server=server,
    )


async def teardown(box: BenchSetup) -> None:
    try:
        await box.target.cleanup(box.prepared)
    finally:
        box._server.stop()


def get_concurrency() -> int:
    """Return the concurrency for this benchmark process.

    ``run_bench`` sets ``BENCH_CONCURRENCY`` for each subprocess. Direct runs
    default to 1.
    """

    raw = os.environ.get("BENCH_CONCURRENCY", "1").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"BENCH_CONCURRENCY must be an integer: {raw!r}") from exc
    if value <= 0:
        raise ValueError("BENCH_CONCURRENCY must be positive")
    return value
