#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Branch Filtering Example - Customer Support System

Demonstrates branch-based filtering with a realistic customer support scenario.
Shows the difference between ALL, PREFIX, and EXACT modes.

Agent Hierarchy:
    CustomerService (BranchFilterMode.EXACT - always) - Main coordinator
    ├── TechnicalSupport (configurable mode) - Handles technical issues
    │   └── DatabaseExpert (same mode as TechnicalSupport) - Specializes in database problems
    └── BillingSupport (same mode as TechnicalSupport) - Handles billing inquiries

Branch naming:
    - CustomerService: "CustomerService"
    - TechnicalSupport: "CustomerService.TechnicalSupport"
    - DatabaseExpert: "CustomerService.TechnicalSupport.DatabaseExpert"
    - BillingSupport: "CustomerService.BillingSupport"

Note: All sub-agents (TechnicalSupport, DatabaseExpert, BillingSupport) use the same filter mode.
"""

import asyncio
import os
import uuid

from trpc_agent_sdk.agents import BranchFilterMode
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


# Simulated backend tools
def check_server_status(service_name: str) -> str:
    """Check the status of a server or service."""
    status_map = {
        "api_server": "✓ API Server: Running (200ms response time)",
        "web_server": "✓ Web Server: Running (150ms response time)",
        "database": "⚠ Database: High CPU usage (85%), experiencing slowdowns",
    }
    return status_map.get(service_name, "✗ Service not found")


def diagnose_database_issue(symptom: str) -> str:
    """Deep diagnosis of database issues."""
    if "slow" in symptom.lower() or "cpu" in symptom.lower():
        return "Database diagnosis: Found 23 slow queries on orders table. Recommendation: Add composite index on (user_id, created_at) columns to improve performance."
    elif "connection" in symptom.lower():
        return "Database diagnosis: Connection pool exhausted. Current: 100/100. Recommendation: Increase pool size to 200."
    return "Database diagnosis: No obvious issues detected. Please provide more details."


def lookup_invoice(customer_id: str) -> str:
    """Look up customer invoice information."""
    return f"Invoice for customer {customer_id}: $299.99 (due date: 2025-12-15, status: PAID, payment method: Credit Card ending in 4242)"


def create_support_system(filter_mode: BranchFilterMode):
    """Create a customer support agent hierarchy.

    CustomerService (EXACT - always) -> TechnicalSupport (filter_mode) -> DatabaseExpert (filter_mode)
                                     -> BillingSupport (filter_mode)

    Args:
        filter_mode: The BranchFilterMode to apply to all sub-agents (TechnicalSupport, DatabaseExpert, BillingSupport)
    """
    model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )

    # DatabaseExpert - Deepest specialist for database issues
    database_expert = LlmAgent(
        name="DatabaseExpert",
        model=model,
        description="Database specialist who diagnoses and fixes database issues",
        instruction="""You are a Database Expert specializing in database performance and troubleshooting.
Use the diagnose_database_issue tool to analyze database problems.
Provide detailed technical recommendations with specific SQL or configuration changes.
After providing your diagnosis, mention what context you have from previous interactions (e.g., can you see the initial server check, billing info, etc.).""",
        tools=[FunctionTool(diagnose_database_issue)],
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        message_branch_filter_mode=filter_mode,
    )

    # TechnicalSupport - Handles technical issues and escalates to DatabaseExpert
    technical_support = LlmAgent(
        name="TechnicalSupport",
        model=model,
        description="Technical support specialist who handles server and system issues",
        instruction="""You are a Technical Support Specialist.
First, use check_server_status to check system health when users report technical issues.
If the issue involves database problems (high CPU, slow queries, connections), transfer to DatabaseExpert for deep diagnosis.
Provide clear technical explanations to customers in simple terms.""",
        tools=[FunctionTool(check_server_status)],
        sub_agents=[database_expert],
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        message_branch_filter_mode=filter_mode,
    )

    # BillingSupport - Handles billing inquiries (sibling to TechnicalSupport)
    billing_support = LlmAgent(
        name="BillingSupport",
        model=model,
        description="Billing specialist who handles payment and invoice questions",
        instruction="""You are a Billing Support Specialist.
Use lookup_invoice to retrieve customer billing information.
After looking up the invoice, mention what context you can see from the conversation history.
Specifically, can you see any technical issues that were discussed earlier? This helps us test branch filtering.""",
        tools=[FunctionTool(lookup_invoice)],
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
        message_branch_filter_mode=filter_mode,
    )

    # CustomerService - Root coordinator
    customer_service = LlmAgent(
        name="CustomerService",
        model=model,
        description="Main customer service coordinator",
        instruction="""You are the Customer Service Coordinator for our tech company.
Route customer requests appropriately:
- Technical issues (server status, database problems, performance, errors) -> TechnicalSupport
- Billing questions (invoice, payment, charges, billing) -> BillingSupport

Be friendly and professional. You have access to full conversation history.""",
        sub_agents=[technical_support, billing_support],
        disallow_transfer_to_parent=True,
        message_branch_filter_mode=BranchFilterMode.EXACT,
    )

    return customer_service


async def run_scenario(mode_name: str, filter_mode: BranchFilterMode):
    """Run a customer support scenario with specified branch filter mode.

    Args:
        mode_name: Display name for the scenario
        filter_mode: The BranchFilterMode to apply to all sub-agents
    """

    print("\n" + "=" * 80)
    print(f"Scenario: {mode_name}")
    print("=" * 80)
    print(f"CustomerService: EXACT (always)")
    print(f"TechnicalSupport: {filter_mode.value}")
    print(f"DatabaseExpert: {filter_mode.value}")
    print(f"BillingSupport: {filter_mode.value}")
    print()

    APP_NAME = f"support_branch_{mode_name.replace(' ', '_')}"
    USER_ID = "customer_12345"
    SESSION_ID = str(uuid.uuid4())

    customer_service = create_support_system(filter_mode)
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=customer_service, session_service=session_service)

    # Realistic customer support conversation
    messages = [
        "Hello, our application is running very slow. Can you check what's wrong?",
        "The database seems to be the problem. Can you diagnose it in detail?",
        "Thanks! Also, can you look up my invoice for customer ID 12345?",
    ]

    for i, message in enumerate(messages, 1):
        print(f"\n{'─' * 80}")
        print(f"Customer Request {i}: {message}")
        print(f"{'─' * 80}")

        user_content = Content(parts=[Part.from_text(text=message)])

        async for event in runner.run_async(user_id=USER_ID, session_id=SESSION_ID, new_message=user_content):
            if event.content and event.content.parts and not event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(f"[{event.author}] {part.text}")
                    elif part.function_call:
                        print(f"[{event.author}] 🔧 Calling: {part.function_call.name}({part.function_call.args})")
                    elif part.function_response:
                        print(f"[{event.author}] 📥 Result: {part.function_response.response}")

    print("\n" + "=" * 80)


async def demo_all_mode():
    """Demonstrate BranchFilterMode.ALL - full context sharing."""
    await run_scenario("ALL Mode", BranchFilterMode.ALL)

    print("\n📝 Analysis - ALL Mode:")
    print("✓ TechnicalSupport can see: CustomerService, TechnicalSupport, DatabaseExpert, BillingSupport")
    print("✓ DatabaseExpert can see: CustomerService, TechnicalSupport, DatabaseExpert, BillingSupport")
    print("✓ BillingSupport can see: CustomerService, TechnicalSupport, DatabaseExpert, BillingSupport")
    print("✓ Result: BillingSupport can reference the technical issues discussed earlier")
    print("✓ Use case: When agents need complete conversation context for better service")


async def demo_prefix_mode():
    """Demonstrate BranchFilterMode.PREFIX - hierarchical visibility."""
    await run_scenario("PREFIX Mode", BranchFilterMode.PREFIX)

    print("\n📝 Analysis - PREFIX Mode:")
    print("✓ TechnicalSupport (CustomerService.TechnicalSupport) can see:")
    print("  - CustomerService messages (parent)")
    print("  - TechnicalSupport messages (self)")
    print("  - DatabaseExpert messages (child)")
    print("  ✗ CANNOT see BillingSupport (sibling branch)")
    print()
    print("✓ DatabaseExpert (CustomerService.TechnicalSupport.DatabaseExpert) can see:")
    print("  - CustomerService messages (ancestor)")
    print("  - TechnicalSupport messages (parent)")
    print("  - DatabaseExpert messages (self)")
    print("  ✗ CANNOT see BillingSupport (unrelated branch)")
    print()
    print("✓ BillingSupport (CustomerService.BillingSupport) can see:")
    print("  - CustomerService messages (parent)")
    print("  - BillingSupport messages (self)")
    print("  ✗ CANNOT see TechnicalSupport or DatabaseExpert (sibling branch)")
    print()
    print("✓ Result: Each department only sees its own hierarchy")
    print("✓ Use case: Departmental isolation with hierarchical context")


async def demo_exact_mode():
    """Demonstrate BranchFilterMode.EXACT - complete isolation."""
    await run_scenario("EXACT Mode", BranchFilterMode.EXACT)

    print("\n📝 Analysis - EXACT Mode:")
    print("✓ TechnicalSupport can ONLY see: TechnicalSupport messages")
    print("✓ DatabaseExpert can ONLY see: DatabaseExpert messages")
    print("✓ BillingSupport can ONLY see: BillingSupport messages")
    print("✗ Each specialist has NO context from other parts of the conversation")
    print("✓ Result: Each agent operates in complete isolation")
    print("✓ Use case: Stateless operations, maximum privacy between departments")


if __name__ == "__main__":
    print("\n🏢 Customer Support System - Branch Filtering Demo")
    print("Demonstrates message_branch_filter_mode in realistic scenarios")
    print("\nAgent Hierarchy:")
    print("  CustomerService (EXACT - always)")
    print("  ├── TechnicalSupport (configurable: ALL/PREFIX/EXACT)")
    print("  │   └── DatabaseExpert (same as TechnicalSupport)")
    print("  └── BillingSupport (same as TechnicalSupport)")

    # Run all demos
    asyncio.run(demo_all_mode())
    asyncio.run(demo_prefix_mode())
    asyncio.run(demo_exact_mode())

    print("\n" + "=" * 80)
    print("🎯 Key Takeaways:")
    print("=" * 80)
    print("1. BranchFilterMode.ALL:")
    print("   - All agents see messages from all branches")
    print("   - Best for scenarios requiring full conversation context")
    print("   - Example: Billing can see technical issues to provide better service")
    print()
    print("2. BranchFilterMode.PREFIX:")
    print("   - Agents see ancestors, self, and descendants only")
    print("   - Enables hierarchical workflows with proper context flow")
    print("   - Example: DatabaseExpert sees CustomerService + TechnicalSupport context")
    print("   - Sibling branches are isolated (TechnicalSupport ✗ BillingSupport)")
    print()
    print("3. BranchFilterMode.EXACT:")
    print("   - Agents only see their own messages")
    print("   - Complete isolation for maximum privacy")
    print("   - Example: Billing department cannot see technical discussions")
    print("=" * 80)
