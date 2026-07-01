"""Fake LLM — 无 API Key 模式下模拟 LLM 响应。

设计思路：
- 基于 case_id 匹配预设的响应映射表
- 支持多种场景：通过、失败、工具调用错误等
- 不产生任何网络请求，所有数据来自配置文件
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FakeLLMResponse:
    """模拟的 LLM 单次响应"""
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"


class FakeLLM:
    """无依赖的假 LLM，用于 pipeline 快速验证。

    使用方式:
        fake = FakeLLM(scenarios={"plate_001": "京A12345"})
        response = await fake.generate("识别 plate_001")
    """

    def __init__(self, scenarios: Optional[dict[str, str]] = None):
        """
        Args:
            scenarios: {case_id: predicted_result} 映射。
                       不传则使用内置默认值。
        """
        self.scenarios = scenarios or self._default_scenarios()
        self.call_count = 0
        self.call_history: list[dict] = []

    @staticmethod
    def _default_scenarios() -> dict[str, str]:
        """内置默认场景 — 覆盖 6 个样例 case"""
        return {
            "train_001": "京A12345",   # 清晰 → 通过
            "train_002": "京A12345",   # 噪声 → 黑名单应命中
            "train_003": "苏A88U88",   # 模糊 → 可能识别错误
            "val_001": "粤B54321",     # 关键 case → 应通过
            "val_002": "苏D13579",     # 噪声+黑名单 → 基线失败
            "val_003": "浙C36912",     # 严重模糊 → 过拟合风险
        }

    async def generate(self, prompt: str) -> FakeLLMResponse:
        """模拟一次 LLM 调用。

        从 prompt 中提取 case_id，返回对应的预设结果。
        若未匹配到 case_id，返回 "UNKNOWN"。
        """
        self.call_count += 1
        case_id = self._extract_case_id(prompt)
        result = self.scenarios.get(case_id, "UNKNOWN")

        response = FakeLLMResponse(content=result)
        self.call_history.append({
            "call": self.call_count,
            "case_id": case_id,
            "result": result,
            "prompt_snippet": prompt[:200],
        })
        return response

    def _extract_case_id(self, prompt: str) -> str:
        """从 prompt 中提取 case_id。"""
        for cid in self.scenarios:
            if cid in prompt:
                return cid
        return "unknown"

    def reset(self):
        """重置调用计数和历史。"""
        self.call_count = 0
        self.call_history.clear()
