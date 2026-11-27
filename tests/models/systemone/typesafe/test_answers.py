# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import json

import httpx2
import pytest

from trpc_agent_sdk.models.systemone import SystemOneError
from trpc_agent_sdk.models.systemone import SystemOneResponse
from trpc_agent_sdk.models.systemone import choice_answer
from trpc_agent_sdk.models.systemone import noul_answer
from trpc_agent_sdk.models.systemone import score_answer


def _response(answers: dict) -> SystemOneResponse:
    raw = json.dumps({
        "model": "test",
        "answers": answers,
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0
        },
    }).encode()
    http = httpx2.Response(
        200,
        content=raw,
        headers={"x-typesafe-request-id": "req"},
        request=httpx2.Request("POST", "https://example.test/v1/systemone"),
    )
    return SystemOneResponse.from_http_response(http)


def test_reads_each_answer_by_name():
    response = _response({
        "refund": {
            "type": "noul",
            "noul": 0.25
        },
        "route": {
            "type": "choice",
            "choice": "billing",
            "confidence": 0.5,
            "probabilities": {
                "billing": 0.7,
                "other": 0.3
            },
        },
        "urgency": {
            "type": "score",
            "score": 1.0,
            "confidence": 0.4,
            "legend": {
                "0": "low",
                "1": "high"
            },
            "probabilities": {
                "0": 0.2,
                "1": 0.8
            },
        },
    })
    assert noul_answer(response, "refund").noul == 0.25
    assert choice_answer(response, "route").choice == "billing"
    assert score_answer(response, "urgency").score == 1.0


def test_missing_answer_names_the_id():
    response = _response({"refund": {"type": "noul", "noul": 0}})
    with pytest.raises(SystemOneError, match='answer "missing" not found'):
        choice_answer(response, "missing")


def test_wrong_type_names_the_id():
    response = _response({"refund": {"type": "noul", "noul": 0}})
    with pytest.raises(SystemOneError, match='answer "refund": expected ChoiceAnswer, got NoulAnswer'):
        choice_answer(response, "refund")
