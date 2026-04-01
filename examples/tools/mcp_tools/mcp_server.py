#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from mcp.server import FastMCP

# 创建MCP服务器
app = FastMCP("simple-tools")


@app.tool()
async def get_weather(location: str) -> str:
    """获取指定地点的天气信息

    Args:
        location: 地点名称

    Returns:
        天气信息字符串
    """
    # 模拟天气数据
    weather_info = {
        "北京": "晴天，温度15°C，湿度45%",
        "上海": "多云，温度18°C，湿度65%",
        "Shenzhen": "小雨，温度25°C，湿度80%",
    }

    return weather_info.get(location, f"{location}的天气信息暂时无法获取")


@app.tool()
async def calculate(operation: str, a: float, b: float) -> float:
    """执行基本数学运算

    Args:
        operation: 运算类型 (add, subtract, multiply, divide)
        a: 第一个数字
        b: 第二个数字

    Returns:
        计算结果
    """
    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y if y != 0 else float("inf"),
    }

    if operation not in operations:
        raise ValueError(f"不支持的运算类型: {operation}")

    return operations[operation](a, b)


if __name__ == "__main__":
    app.run(transport="stdio")
    # app.run(transport="sse")
    # app.run(transport="streamable-http")
