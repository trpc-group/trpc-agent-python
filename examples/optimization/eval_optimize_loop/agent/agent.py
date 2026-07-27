# -*- coding: utf-8 -*-
# Copyright @ 2025 Tencent.com
"""Shopping assistant agent with 4 tools for evaluation + optimization pipeline.

Tools:
    - get_product_price(city, product)   → price info
    - check_stock(product)               → stock status
    - get_discount(product)              → discount info
    - get_shipping(product, city)        → shipping availability

Baseline prompt (system.md) is deliberately vague —
"friendly chat assistant" without format instructions.
The optimizer must learn to add output format constraints.
"""

from pathlib import Path
from typing import Any, Dict

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel, OpenAIModel
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import GenerateContentConfig

from .config import get_model_config


# ---- Prompt file paths ----
SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"
SKILL_PATH = Path(__file__).parent / "prompts" / "skill.md"


# ---- Tool implementations ----

def get_product_price(city: str, product: str) -> Dict[str, Any]:
    """查询指定城市某商品的当前价格。

    Args:
        city: 城市名称
        product: 商品名称
    """
    prices = {
        ("上海", "苹果"): {"price": 5, "unit": "元/斤"},
        ("上海", "香蕉"): {"price": 4, "unit": "元/斤"},
        ("北京", "苹果"): {"price": 6, "unit": "元/斤"},
        ("北京", "香蕉"): {"price": 4.5, "unit": "元/斤"},
        ("深圳", "橘子"): {"price": 5.5, "unit": "元/斤"},
        ("深圳", "苹果"): {"price": 7, "unit": "元/斤"},
    }
    result = prices.get((city, product), {"price": 5, "unit": "元/斤"})
    return {"city": city, "product": product, **result}


def check_stock(product: str) -> Dict[str, Any]:
    """查询某商品当前库存状态。

    Args:
        product: 商品名称
    """
    stock_data = {
        "苹果": {"status": "充足", "quantity": 500},
        "香蕉": {"status": "充足", "quantity": 300},
        "橘子": {"status": "紧张", "quantity": 50},
    }
    result = stock_data.get(product, {"status": "充足", "quantity": 200})
    return {"product": product, **result}


def get_discount(product: str) -> Dict[str, Any]:
    """查询某商品当前折扣信息。

    Args:
        product: 商品名称
    """
    discounts = {
        "苹果": {"discount": "9折", "original_price": 5, "sale_price": 4.5},
        "香蕉": {"discount": "无折扣", "original_price": 4, "sale_price": 4},
        "橘子": {"discount": "8折", "original_price": 5.5, "sale_price": 4.4},
    }
    result = discounts.get(product, {"discount": "无折扣", "original_price": 0, "sale_price": 0})
    return {"product": product, **result}


def get_shipping(product: str, city: str) -> Dict[str, Any]:
    """查询某商品是否能配送到指定城市。

    Args:
        product: 商品名称
        city: 目标城市名称
    """
    shipping_data = {
        ("苹果", "杭州"): {"available": True, "eta": "1-2天", "fee": 5},
        ("香蕉", "杭州"): {"available": True, "eta": "2-3天", "fee": 8},
        ("苹果", "深圳"): {"available": True, "eta": "1天", "fee": 3},
        ("橘子", "杭州"): {"available": False, "eta": None, "fee": None},
    }
    key = (product, city)
    result = shipping_data.get(
        key, {"available": True, "eta": "3-5天", "fee": 10}
    )
    return {"product": product, "city": city, **result}


# ---- Model & Agent factory ----

def _create_model() -> LLMModel:
    """Build OpenAI-compatible model from env vars."""
    api_key, base_url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=base_url)


def _read_instruction() -> str:
    """Read system + skill prompts from disk (re-read each call for GEPA)."""
    system = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8").strip()
    skill = SKILL_PATH.read_text(encoding="utf-8").strip()
    return f"{system}\n\n## 技能说明\n{skill}"


def _create_agent_with_prompts(instruction: str) -> LlmAgent:
    """Build LlmAgent with given instruction string."""
    return LlmAgent(
        name="shopping_assistant",
        description="购物助手，可查价格、库存、折扣、配送信息",
        model=_create_model(),
        instruction=instruction,
        generate_content_config=GenerateContentConfig(
            temperature=0.2,
            top_p=0.9,
            max_output_tokens=1024,
        ),
        tools=[
            FunctionTool(get_product_price),
            FunctionTool(check_stock),
            FunctionTool(get_discount),
            FunctionTool(get_shipping),
        ],
    )


def create_agent() -> LlmAgent:
    """Build a fresh LlmAgent reading current prompt files from disk."""
    return _create_agent_with_prompts(_read_instruction())


# Module-level root_agent (for AgentEvaluator with agent_module="agent")
root_agent = create_agent()
