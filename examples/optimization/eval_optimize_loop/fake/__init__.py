"""Fake 模块公共导出"""
# LEGACY: FakeLLM is not used by the current pipeline (baseline fake mode
# reads FAKE_PREDICTIONS directly). Exported for external reuse only;
# may be removed in a future cleanup.
from .fake_model import FakeLLM, FakeLLMResponse
from .fake_judge import FakeJudge, JudgeResult, JudgeScore

__all__ = [
    "FakeLLM",
    "FakeLLMResponse",
    "FakeJudge",
    "JudgeResult",
    "JudgeScore",
]
