# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Agent module"""

from trpc_agent_sdk.agents import BranchFilterMode
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import FunctionTool

from .config import get_model_config
from .prompts import BILLING_SUPPORT_INSTRUCTION
from .prompts import CUSTOMER_SERVICE_INSTRUCTION
from .prompts import DATABASE_EXPERT_INSTRUCTION
from .prompts import TECHNICAL_SUPPORT_INSTRUCTION
from .tools import check_server_status
from .tools import diagnose_database_issue
from .tools import lookup_invoice


def _create_model() -> LLMModel:
    """ Create a model"""
    api_key, url, model_name = get_model_config()
    model = OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)
    return model


def create_agent(filter_mode: BranchFilterMode = BranchFilterMode.ALL) -> LlmAgent:
    """Create a customer support agent hierarchy.

    Agent Hierarchy:
        CustomerService (BranchFilterMode.EXACT - always) - Main coordinator
        ├── TechnicalSupport (filter_mode) - Handles technical issues
        │   └── DatabaseExpert (filter_mode) - Specializes in database problems
        └── BillingSupport (filter_mode) - Handles billing inquiries

    Args:
        filter_mode: The BranchFilterMode to apply to all sub-agents
                     (TechnicalSupport, DatabaseExpert, BillingSupport).
    """
    model = _create_model()

    database_expert = LlmAgent(
        name="DatabaseExpert",
        model=model,
        description="Database specialist who diagnoses and fixes database issues",
        instruction=DATABASE_EXPERT_INSTRUCTION,
        tools=[FunctionTool(diagnose_database_issue)],
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        message_branch_filter_mode=filter_mode,
    )

    technical_support = LlmAgent(
        name="TechnicalSupport",
        model=model,
        description="Technical support specialist who handles server and system issues",
        instruction=TECHNICAL_SUPPORT_INSTRUCTION,
        tools=[FunctionTool(check_server_status)],
        sub_agents=[database_expert],
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        message_branch_filter_mode=filter_mode,
    )

    billing_support = LlmAgent(
        name="BillingSupport",
        model=model,
        description="Billing specialist who handles payment and invoice questions",
        instruction=BILLING_SUPPORT_INSTRUCTION,
        tools=[FunctionTool(lookup_invoice)],
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        message_branch_filter_mode=filter_mode,
    )

    customer_service = LlmAgent(
        name="CustomerService",
        model=model,
        description="Main customer service coordinator",
        instruction=CUSTOMER_SERVICE_INSTRUCTION,
        sub_agents=[technical_support, billing_support],
        disallow_transfer_to_parent=True,
        message_branch_filter_mode=BranchFilterMode.EXACT,
    )

    return customer_service


root_agent = create_agent()
