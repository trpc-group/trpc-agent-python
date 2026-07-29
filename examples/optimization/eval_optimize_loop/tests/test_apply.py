#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2025 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""验证 Champion prompt 在 dry-run / apply REJECT / apply ACCEPT 三种情况下的写回行为。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio


def _sha(p: Path) -> str:
    import runner

    return runner.sha256_file(p)


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch):
    for k in ("OPENAI_API_KEY", "TRPC_AGENT_API_KEY", "TRPC_AGENT_BASE_URL", "TRPC_AGENT_MODEL_NAME"):
        monkeypatch.delenv(k, raising=False)


async def test_dry_run_keeps_champion_unchanged(loop_root: Path) -> None:
    champion = loop_root / "prompts" / "system.md"
    before = _sha(champion)
    import pipeline

    await pipeline.amain(["--mode", "fake", "--scenario", "success"])
    assert _sha(champion) == before


async def test_apply_rejected_keeps_champion_unchanged(loop_root: Path) -> None:
    champion = loop_root / "prompts" / "system.md"
    before = _sha(champion)
    import pipeline

    rc = await pipeline.amain(["--mode", "fake", "--scenario", "no_effect", "--apply"])
    # REJECT + --apply -> exit 2
    assert rc == 2
    assert _sha(champion) == before


async def test_apply_accepted_writes_champion(loop_root: Path) -> None:
    """success 场景 + --apply：源 prompt 应被 Challenger 覆盖。

    跑完后立即恢复原 Champion 内容，避免污染其他测试。
    恢复也必须通过 TargetPrompt，避免绕过生产写入抽象。
    """
    champion = loop_root / "prompts" / "system.md"
    original_text = champion.read_text(encoding="utf-8")
    original_sha = _sha(champion)
    try:
        import pipeline

        rc = await pipeline.amain(["--mode", "fake", "--scenario", "success", "--apply"])
        assert rc == 0
        new_sha = _sha(champion)
        # 必须变了
        assert new_sha != original_sha
        # 报告中记录的 after_apply_sha256 必须等于 challenger_sha256
        import json

        report = json.loads((pipeline._HERE / "optimization_report.json").read_text(encoding="utf-8"))
        assert report["audit"]["applied"] is True
        assert report["audit"]["after_apply_sha256"] == report["frozen"]["challenger_sha256"]
    finally:
        from trpc_agent_sdk.evaluation import TargetPrompt

        target = TargetPrompt().add_path("system", str(champion))
        await target.write_all({"system": original_text})
        assert _sha(champion) == original_sha


async def test_champion_restored_when_challenger_evaluation_crashes(
    loop_root: Path,
    monkeypatch,
) -> None:
    """Exercise the optimize-mode prompt swap, then fail mid-evaluation."""

    import fake_agent
    import runner
    from trpc_agent_sdk.evaluation import EvaluateResult

    champion = loop_root / "prompts" / "system.md"
    before = _sha(champion)
    challenger = fake_agent.build_candidate("success")
    calls = 0

    async def evaluator_stub(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            assert champion.read_text(encoding="utf-8") == challenger
            raise RuntimeError("simulated challenger evaluator crash")
        return EvaluateResult()

    async def call_agent_stub(query: str) -> str:
        return query

    monkeypatch.setattr(runner, "_run_evaluator", evaluator_stub)
    with pytest.raises(RuntimeError, match="simulated challenger evaluator crash"):
        await runner.run_pair(
            champion_prompt_path=champion,
            challenger_text=challenger,
            train_evalset_path=loop_root / "data" / "train.evalset.json",
            val_evalset_path=loop_root / "data" / "val.evalset.json",
            metric_config_path=loop_root / "data" / "test_config.json",
            artifact_root=loop_root / "runs",
            mode="optimize",
            candidate_source="agent_optimizer",
            scenario=None,
            seed=42,
            call_agent=call_agent_stub,
        )
    assert _sha(champion) == before
