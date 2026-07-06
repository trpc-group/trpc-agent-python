# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""「城市信息助手」的两个业务工具 —— eval_optimize_loop 专用。

这两个工具是普通同步函数，由 ``FunctionTool`` 包装后注册到 LlmAgent：

- ``convert_distance``：公里→米换算。只接受规范单位 ``"km"``；传入其它单位
  （例如 baseline prompt 会让 agent 传中文 ``"公里"``）时返回 error dict。
  这个"单位必须归一化"的约束正是 baseline 的失败点之一（wrong_tool_args）。
- ``knowledge_search``：内置小型城市知识库（来源标记 ``city-guide``）。
  ``llm_rubric_knowledge_recall`` 裁判会检查它的返回里是否带 ``city-guide``
  来源，因此工具名必须与 eval 配置里的 ``knowledge_tool_names`` 完全一致。

两个工具都是纯函数、无外部依赖，保证离线可跑且逐次运行结果确定。
"""

from __future__ import annotations

from typing import Any

# 城市知识库：knowledge_search 的返回与 evalset 中期望回答共用这份文案，
# 保证「工具返回 → agent 引用 → exact 匹配」链路字符级一致。
CITY_CORPUS: dict[str, str] = {
    "深圳": "深圳是一座以科技创新闻名的现代化滨海城市。",
    "杭州": "杭州是一座以西湖和数字经济闻名的历史文化名城。",
    "北京": "北京是一座历史悠久的文化古都。",
}


def convert_distance(value: float, unit: str) -> dict[str, Any]:
    """距离换算工具：把公里换算成米。

    Args:
        value: 数值（公里）。
        unit: 单位，必须是规范写法 "km"；其它写法（如 "公里"）视为不支持。

    Returns:
        成功: {"meters": value * 1000}；单位不规范: {"error": "..."}。
    """
    if unit == "km":
        meters = value * 1000
        # 整数值转 int，避免 3000.0 之类的浮点尾巴影响可读性
        if float(meters).is_integer():
            meters = int(meters)
        return {"meters": meters}
    return {"error": f"unsupported unit: {unit}，请使用规范单位 km"}


def knowledge_search(query: str) -> dict[str, Any]:
    """知识检索工具：按城市名查询内置城市指南（来源 city-guide）。

    Args:
        query: 检索词（城市名）。

    Returns:
        命中: {"source": "city-guide", "summary": "<一句话简介>"}；
        未命中: {"source": "none", "summary": ""}。
    """
    for city, summary in CITY_CORPUS.items():
        if city in query:
            return {"source": "city-guide", "summary": summary}
    return {"source": "none", "summary": ""}
