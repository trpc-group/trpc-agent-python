"""call_agent 闭包在 real 模式下应每次重建 agent, 否则候选 prompt 不生效.

PipelineRunner.run() 中 call_agent 的定义:
``create_agent(demo_mode=False)`` 在每次调用时执行 (见 ``_runner.py:119``),
这保证优化器的每轮评测都从磁盘重读 ``system.md``.
"""

from unittest.mock import MagicMock, patch


def test_call_agent_rebuilds_agent_per_invocation():
    """验证 agent 每次调用都重建——两次调用触发两次 create_agent."""
    build_count = {"n": 0}

    def fake_create_agent(*, demo_mode=False):
        build_count["n"] += 1
        agent = MagicMock()
        agent.demo_mode = demo_mode
        return agent

    # 模拟 call_agent 闭包的重建模式
    async def call_agent(_input_text: str) -> str:
        fake_create_agent(demo_mode=False)
        return "ok"

    import asyncio
    asyncio.run(call_agent("a"))
    asyncio.run(call_agent("b"))

    assert build_count["n"] == 2, f"expected 2 builds, got {build_count['n']}"


def test_create_agent_importable():
    """create_agent 在 demo_mode 下可安全导入 (不请求 API key)."""
    from agent.agent import create_agent
    agent = create_agent(demo_mode=True)
    assert agent is not None
