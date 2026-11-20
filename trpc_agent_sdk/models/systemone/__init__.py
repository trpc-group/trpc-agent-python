# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""System One client for the official TypeSafe API and for Laya HTTP.

``SystemOneClient.typesafe`` calls the official API. It requires an API key
and always sends a model. ``SystemOneClient.laya`` calls a ``laya-serve``
process. It can omit both, which lets Laya choose the checkpoint and accept
anonymous local requests.

Questions and answers use the official SDK types. Read one answer with
``noul_answer``, ``choice_answer``, or ``score_answer``. A Laya response also
carries ``routing``. ``system_one_batch`` is only available on the Laya
backend. Import the raw SDK from ``typesafe_sdk``.

Example::

    from trpc_agent_sdk.models.systemone import Choice
    from trpc_agent_sdk.models.systemone import Noul
    from trpc_agent_sdk.models.systemone import Score
    from trpc_agent_sdk.models.systemone import SystemOneClient

    async with SystemOneClient.laya("http://127.0.0.1:8000") as client:
        response = await client.system_one(
            {"body": "We were billed twice. Please refund the duplicate."},
            {
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
            },
        )
"""

from typesafe_sdk import Choice
from typesafe_sdk import ChoiceAnswer
from typesafe_sdk import Noul
from typesafe_sdk import NoulAnswer
from typesafe_sdk import NoulCriteria
from typesafe_sdk import RetryPolicy
from typesafe_sdk import Score
from typesafe_sdk import ScoreAnswer
from typesafe_sdk import SystemOneResponse
from typesafe_sdk import TypeSafeAPIError
from typesafe_sdk import TypeSafeError
from typesafe_sdk import Usage
from ._answers import choice_answer
from ._answers import noul_answer
from ._answers import score_answer
from ._client import SystemOneClient
from ._errors import SystemOneError
from ._laya import LayaRouting
from ._laya import LayaSystemOneResponse

__all__ = [
    "Choice",
    "ChoiceAnswer",
    "LayaRouting",
    "LayaSystemOneResponse",
    "Noul",
    "NoulAnswer",
    "NoulCriteria",
    "RetryPolicy",
    "Score",
    "ScoreAnswer",
    "SystemOneClient",
    "SystemOneError",
    "SystemOneResponse",
    "TypeSafeAPIError",
    "TypeSafeError",
    "Usage",
    "choice_answer",
    "noul_answer",
    "score_answer",
]
