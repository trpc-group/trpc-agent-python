# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Classify the same support ticket with the TypeSafe API and with Laya."""

import asyncio

from dotenv import load_dotenv
from trpc_agent_sdk.models.systemone import Choice
from trpc_agent_sdk.models.systemone import LayaSystemOneResponse
from trpc_agent_sdk.models.systemone import Noul
from trpc_agent_sdk.models.systemone import Score
from trpc_agent_sdk.models.systemone import SystemOneClient
from trpc_agent_sdk.models.systemone import SystemOneResponse
from trpc_agent_sdk.models.systemone import choice_answer
from trpc_agent_sdk.models.systemone import noul_answer
from trpc_agent_sdk.models.systemone import score_answer

from config import LayaSettings
from config import TypeSafeSettings
from config import laya_settings
from config import typesafe_settings

load_dotenv()

QUESTIONS = {
    "department": Choice(
        instructions="Which team should handle this?",
        criteria={
            "billing": "invoices, payments, refunds",
            "technical": "bugs, outages",
            "other": "everything else",
        },
    ),
    "urgency": Score(
        instructions="How urgent is this?",
        criteria=["not urgent", "soon", "blocking"],
    ),
    "refund": Noul(instructions="Does the user request a refund?"),
}

TICKET = {"body": "We were billed twice. Please refund the duplicate."}
BATCH_TICKETS = [
    TICKET,
    {"body": "The dashboard has been down since this morning."},
]


def print_answers(response: SystemOneResponse) -> None:
    """Print the three ticket answers plus model, usage, and Laya routing."""
    print(f"department: {choice_answer(response, 'department').choice}")
    print(f"urgency: {score_answer(response, 'urgency').score}")
    print(f"refund: {noul_answer(response, 'refund').noul}")
    print(f"model: {response.model}")
    usage = response.usage
    print(f"usage: input_tokens={usage.input_tokens} output_tokens={usage.output_tokens}")
    if isinstance(response, LayaSystemOneResponse) and response.routing is not None:
        routing = response.routing
        repo = f" repo={routing.repo}" if routing.repo else ""
        print(f"routing: model={routing.model}{repo}")


def print_state(state: dict[str, str]) -> None:
    """Print the ticket text that was sent as ``state``."""
    print(f"state: {state['body']}")


async def run_typesafe(settings: TypeSafeSettings) -> None:
    """Evaluate one ticket on the official TypeSafe API."""
    async with SystemOneClient.typesafe(
        api_key=settings.api_key,
        base_url=settings.base_url,
        default_model=settings.default_model,
    ) as client:
        response = await client.system_one(TICKET, QUESTIONS)
    print_state(TICKET)
    print_answers(response)


async def run_laya(settings: LayaSettings) -> None:
    """Evaluate one ticket, then a two-ticket batch, on a Laya server."""
    async with SystemOneClient.laya(
        settings.base_url,
        api_key=settings.api_key,
        default_model=settings.default_model,
    ) as client:
        response = await client.system_one(TICKET, QUESTIONS)
        batch = await client.system_one_batch(BATCH_TICKETS, QUESTIONS)
    print_state(TICKET)
    print_answers(response)
    print("--- batch ---")
    for index, (state, item) in enumerate(zip(BATCH_TICKETS, batch), start=1):
        print(f"[{index}/{len(BATCH_TICKETS)}]")
        print_state(state)
        print_answers(item)


async def _run_typesafe() -> bool:
    """Run TypeSafe when ``TYPESAFE_API_KEY`` is set."""
    settings = typesafe_settings()
    if settings is None:
        print("[SKIP] TYPESAFE_API_KEY is not set")
        return False
    await run_typesafe(settings)
    return True


async def _run_laya() -> bool:
    """Run Laya when ``LAYA_BASE_URL`` is set."""
    settings = laya_settings()
    if settings is None:
        print("[SKIP] LAYA_BASE_URL is not set")
        return False
    await run_laya(settings)
    return True


async def main() -> None:
    """Run TypeSafe and Laya, skipping a client whose required setting is missing."""
    attempted = 0
    succeeded = 0
    for label, runner in (("typesafe", _run_typesafe), ("laya", _run_laya)):
        print("=" * 60)
        print(f"Backend: {label}")
        print("=" * 60)
        try:
            sent = await runner()
        except Exception as exc:
            print(f"[FAIL] {exc}")
            attempted += 1
            continue
        if not sent:
            continue
        attempted += 1
        succeeded += 1
        print("-" * 40)
    if attempted == 0:
        raise SystemExit("No backend ran. Set TYPESAFE_API_KEY and/or LAYA_BASE_URL.")
    if succeeded == 0:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
