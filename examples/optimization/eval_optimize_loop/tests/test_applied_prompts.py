"""applied_prompts 在 yield 内抛异常时仍还原 baseline.

把最佳候选 prompt 写入磁盘是 Stage 4 的正确性前提 (optimization.md §3.3 FAQ):
``AgentOptimizer.optimize(update_source=False)`` 在 finally 块把源文件回滚成 baseline,
若不显式写回候选, Stage 4 评的就是 baseline 而非候选。
"""

import asyncio
from pathlib import Path
import tempfile

from trpc_agent_sdk.evaluation._target_prompt import TargetPrompt

from pipeline._eval_backend import applied_prompts


def test_applied_prompts_restores_baseline_on_exception():
    with tempfile.TemporaryDirectory() as tmp:
        prompt_path = Path(tmp) / "system.md"
        prompt_path.write_text("BASELINE", encoding="utf-8")

        target = TargetPrompt().add_path("system_prompt", str(prompt_path))

        async def _run():
            async with applied_prompts(target, {"system_prompt": "CANDIDATE"}):
                assert prompt_path.read_text(encoding="utf-8") == "CANDIDATE"
                raise RuntimeError("simulated evaluate failure")

        try:
            asyncio.run(_run())
        except RuntimeError:
            pass

        assert prompt_path.read_text(encoding="utf-8") == "BASELINE"


def test_applied_prompts_restores_on_success():
    """正常退出也应还原 baseline, 不留下候选污染后续运行."""
    with tempfile.TemporaryDirectory() as tmp:
        prompt_path = Path(tmp) / "system.md"
        prompt_path.write_text("BASELINE", encoding="utf-8")

        target = TargetPrompt().add_path("system_prompt", str(prompt_path))

        async def _run():
            async with applied_prompts(target, {"system_prompt": "CANDIDATE"}):
                assert prompt_path.read_text(encoding="utf-8") == "CANDIDATE"

        asyncio.run(_run())
        assert prompt_path.read_text(encoding="utf-8") == "BASELINE"
