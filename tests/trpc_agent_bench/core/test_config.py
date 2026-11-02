"""Benchmark configuration objects."""

from __future__ import annotations

from types import SimpleNamespace

from trpc_agent_bench.core import AgentBenchConf
from trpc_agent_bench.core import ModelConf


def test_agent_bench_conf_defaults() -> None:
    conf = AgentBenchConf()

    assert conf.app_name == "bench_app"
    assert conf.user_id == "bench_user"
    assert conf.agent_conf is None
    assert conf.model_conf == ModelConf()
    assert conf.base_url is None
    assert not hasattr(conf, "session_service")


def test_agent_bench_conf_keeps_agent_conf() -> None:
    agent_conf = SimpleNamespace(skills_path="/tmp/skills")
    conf = AgentBenchConf(
        app_name="my_app",
        user_id="my_user",
        agent_conf=agent_conf,
    )

    assert conf.app_name == "my_app"
    assert conf.user_id == "my_user"
    assert conf.agent_conf is agent_conf


def test_model_conf_is_mutable() -> None:
    conf = ModelConf()
    conf.request_delay_ms = 10
    conf.chunk_delay_ms = 2

    assert conf.request_delay_ms == 10
    assert conf.chunk_delay_ms == 2
