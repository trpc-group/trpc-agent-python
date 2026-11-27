# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import json

import httpx2
import pytest

from trpc_agent_sdk.models.systemone import Choice
from trpc_agent_sdk.models.systemone import Noul
from trpc_agent_sdk.models.systemone import Score
from trpc_agent_sdk.models.systemone import SystemOneClient
from trpc_agent_sdk.models.systemone import SystemOneError
from trpc_agent_sdk.models.systemone import SystemOneResponse
from trpc_agent_sdk.models.systemone._validate import check_answers

_URL = "https://systemone.test"
_BASE_URLS = [
    "",
    "localhost:8000",
    "ftp://host",
    "https://",
    "https://host/%zz",
    "https://user:secret@host",
    "https://host?q=x",
    "https://host?",
    "https://host#x",
    "https://host#",
]


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
        request=httpx2.Request("POST", _URL + "/v1/systemone"),
    )
    return SystemOneResponse.from_http_response(http)


def _noul_body() -> bytes:
    return json.dumps({
        "model": "test",
        "answers": {
            "q": {
                "type": "noul",
                "noul": 0
            }
        },
        "usage": {
            "input_tokens": 0,
            "output_tokens": 0
        },
    }).encode()


@pytest.mark.parametrize("base_url", _BASE_URLS)
def test_invalid_base_url(base_url):
    with pytest.raises(SystemOneError, match="invalid base url"):
        SystemOneClient.laya(base_url)
    with pytest.raises(SystemOneError, match="invalid base url"):
        SystemOneClient.typesafe(api_key="test-key", base_url=base_url)


def test_invalid_timeout():
    with pytest.raises(SystemOneError, match="timeout"):
        SystemOneClient.laya(_URL, timeout=-1)
    with pytest.raises(SystemOneError, match="timeout"):
        SystemOneClient.laya(_URL, timeout=True)


async def test_invalid_questions_do_not_reach_the_server():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx2.Response(200, content=_noul_body())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.laya(_URL, http_client=http)
        with pytest.raises(SystemOneError, match="state is required"):
            await client.system_one(None, {"q": Noul(instructions="judge")})
        with pytest.raises(SystemOneError, match="at least one question"):
            await client.system_one("hello", {})
        with pytest.raises(SystemOneError, match="instructions must not be blank"):
            await client.system_one("hello", {"q": {"type": "noul", "instructions": "  "}})
        with pytest.raises(SystemOneError, match="question id must not be blank"):
            await client.system_one("hello", {" ": Noul(instructions="judge")})
        with pytest.raises(SystemOneError, match="criteria must not be empty"):
            await client.system_one("hello", {"q": {"type": "choice", "instructions": "choose", "criteria": {}}})
    assert calls == 0


def test_answer_mismatches():
    choice = {"q": Choice(instructions="choose", criteria={"a": None, "b": None})}
    with pytest.raises(SystemOneError, match="not found"):
        check_answers(choice, _response({}))
    with pytest.raises(SystemOneError, match="expected choice, got noul"):
        check_answers(choice, _response({"q": {"type": "noul", "noul": 1}}))
    with pytest.raises(SystemOneError, match="choice 'z' is not in criteria"):
        check_answers(
            choice,
            _response({"q": {
                "type": "choice",
                "choice": "z",
                "confidence": 1,
                "probabilities": {
                    "a": 1
                },
            }}),
        )
    with pytest.raises(SystemOneError, match="probability 'z' is not in criteria"):
        check_answers(
            choice,
            _response(
                {"q": {
                    "type": "choice",
                    "choice": "a",
                    "confidence": 1,
                    "probabilities": {
                        "a": 0.5,
                        "z": 0.5
                    },
                }}),
        )
    score = {"q": Score(instructions="rate", criteria=["low", "high"])}
    with pytest.raises(SystemOneError, match="expected 2 score levels, got 1"):
        check_answers(
            score,
            _response({
                "q": {
                    "type": "score",
                    "score": 0,
                    "confidence": 1,
                    "legend": {
                        "0": "low"
                    },
                    "probabilities": {
                        "0": 1
                    },
                }
            }),
        )
