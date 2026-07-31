"""从环境变量读取模型配置。

两种模式：
  Real 模式（--no-demo-mode）：需要设置以下环境变量
    - TRPC_AGENT_API_KEY: API 密钥
    - TRPC_AGENT_BASE_URL: API 基础 URL
    - TRPC_AGENT_MODEL_NAME: 模型名称（默认 gpt-4o-mini）
  Demo 模式（--demo-mode，默认）：不需要任何环境变量，使用预录制 trace 数据
调用方式:
    api_key, base_url, model_name = get_model_config(demo_mode=True)
"""

import os


def get_model_config(demo_mode: bool = True) -> tuple[str, str, str]:
    """从环境变量读取模型配置。

    Args:
        demo_mode: 是否为 demo 模式。demo 模式下不需要 API key。
    Returns:
        (api_key, base_url, model_name) 元组。
    Raises:
        ValueError: Real 模式下缺少必需的环境变量时抛出。
    """
    api_key = os.environ.get("TRPC_AGENT_API_KEY", "")
    base_url = os.environ.get("TRPC_AGENT_BASE_URL", "")
    model_name = os.environ.get("TRPC_AGENT_MODEL_NAME", "gpt-4o-mini")

    if demo_mode:
        return api_key, base_url, model_name

    if not api_key or not base_url:
        raise ValueError(
            "Real 模式下必须设置 TRPC_AGENT_API_KEY 和 TRPC_AGENT_BASE_URL 环境变量。\n"
            "例如: export TRPC_AGENT_API_KEY=your_key\n"
            "      export TRPC_AGENT_BASE_URL=https://api.openai.com/v1\n"
            "Demo 模式请使用 --demo-mode 标志（无需 API key）。"
        )

    return api_key, base_url, model_name
