"""电商购物助手 agent 定义。

创建配备了 6 个电商工具（搜索商品、查看详情、添加到购物车、查询订单、
查看购物车、应用优惠券）的 LlmAgent 实例。

Agent 的 system prompt 从磁盘动态读取，支持 AgentOptimizer 热更新——
优化后的 prompt 写入 agent/prompts/system.md 后，agent 自动使用新 prompt。

两种模式：
  - Demo 模式（demo_mode=True）: 使用空凭证创建 agent，配合 trace 模式评测使用，
    无需 API key。评测从预录制轨迹计算 metric，不会实际调用 LLM。
  - Real 模式（demo_mode=False）: 从环境变量读取 API key/base_url/model_name，
    实际调用 LLM 进行推理和优化。

调用方式:
    from agent.agent import create_agent

    # Demo 模式（无需 API key）
    agent = create_agent(demo_mode=True)

    # Real 模式（需要 API key）
    agent = create_agent(demo_mode=False)
"""

from pathlib import Path

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config
from .tools import (
    add_to_cart,
    apply_coupon,
    check_order_status,
    get_cart,
    get_product_details,
    search_products,
)

_HERE = Path(__file__).parent
SYSTEM_PROMPT_PATH = _HERE / "prompts" / "system.md"


def _read_system_prompt() -> str:
    """从磁盘读取系统 prompt，支持 AgentOptimizer 热更新。

    每次调用时重新读取文件，因此优化器写入新的 prompt 后立即生效。
    """
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def create_agent(demo_mode: bool = True) -> LlmAgent:
    """创建电商购物助手 agent 实例。

    Args:
        demo_mode: True 时使用空凭证（配合 trace 模式评测，无需 API key）；
                   False 时从环境变量读取 API key/base_url/model_name。

    Returns:
        LlmAgent 实例，可直接用于评测和优化。
    """
    api_key, base_url, model_name = get_model_config(demo_mode=demo_mode)

    model = OpenAIModel(
        model_name=model_name,
        api_key=api_key,
        base_url=base_url,
    )

    return LlmAgent(
        name="shopping_assistant",
        description="电商购物助手：帮助用户搜索商品、查看详情、管理购物车和追踪订单。",
        model=model,
        instruction=_read_system_prompt(),
        tools=[
            FunctionTool(search_products),
            FunctionTool(get_product_details),
            FunctionTool(add_to_cart),
            FunctionTool(check_order_status),
            FunctionTool(get_cart),
            FunctionTool(apply_coupon),
        ],
        generate_content_config=GenerateContentConfig(
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=1024,
        ),
    )


# 模块级 agent 实例，默认 demo 模式（向后兼容）
root_agent = create_agent(demo_mode=True)
