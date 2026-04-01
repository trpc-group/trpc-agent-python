# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
This is a Sub-Agents example for an intelligent routing system.

Main Workflow: Coordinator → Specialist Agent.
"""

import uuid

from trpc_agent_sdk.agents import BranchFilterMode
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


async def get_product_info(product_type: str) -> str:
    """Tool function for retrieving product information"""
    products = {
        "speakers": "Smart Speaker Pro - Voice control, AI assistant - $199",
        "displays": "Smart Display 10 - Touch screen, video calls - $399",
        "security": "Home Security System - 24/7 monitoring, mobile alerts - $599"
    }
    return products.get(product_type, f"Product type '{product_type}' not found")


async def generate_consult_id() -> str:
    """Generate a unique consultation ID. When a customer contacts us, we must generate a unique consultation ID."""
    return str(uuid.uuid4())


async def check_system_status(device: str) -> str:
    """Tool function for checking system status"""
    return f"System diagnostic for {device}: Status OK, all functions normal"


def create_agent():
    """Create an intelligent customer service system with sub-agents"""

    model = _create_model()

    # Technical Support Agent
    technical_support_agent = LlmAgent(
        name="technical_support",
        model=model,
        description="Technical support specialist",
        instruction="""You are a technical support specialist.
Help with device troubleshooting and system diagnostics.
Use check_system_status tool to check device status.""",
        tools=[FunctionTool(check_system_status)],
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        output_key="technical_result",
        message_branch_filter_mode=BranchFilterMode.
        EXACT  # Fully isolated, containing only the messages of the current Agent
    )

    # Sales Consultant Agent
    sales_consultant_agent = LlmAgent(
        name="sales_consultant",
        model=model,
        description="Sales consultant for product information",
        instruction="""You are a sales consultant. Help customers with product information.
Use get_product_info tool with: speakers, displays, or security.""",
        tools=[FunctionTool(get_product_info)],
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        output_key="sales_result",
    )

    # Main Customer Service Coordinator Agent
    customer_service_coordinator = LlmAgent(
        name="customer_service_coordinator",
        model=model,
        description="Customer service coordinator that routes inquiries",
        instruction="""You are a customer service coordinator.
First you should invoke generate_consult_id tool to generate a unique consultation ID.
And then Route customer inquiries:
- Technical issues → transfer to technical_support
- Product questions → transfer to sales_consultant""",
        sub_agents=[technical_support_agent, sales_consultant_agent],
        output_key="coordinator_result",
        tools=[FunctionTool(generate_consult_id)],
    )

    return customer_service_coordinator


root_agent = create_agent()
