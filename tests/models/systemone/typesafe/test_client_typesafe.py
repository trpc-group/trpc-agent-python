# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import asyncio
import json
from pathlib import Path

import httpx2
import pytest
from typesafe_sdk import TypeSafeAPIConnectionError
from typesafe_sdk import TypeSafeAPITimeoutError
from typesafe_sdk import TypeSafeRateLimitError

from trpc_agent_sdk.models.systemone import Choice
from trpc_agent_sdk.models.systemone import Noul
from trpc_agent_sdk.models.systemone import RetryPolicy
from trpc_agent_sdk.models.systemone import Score
from trpc_agent_sdk.models.systemone import SystemOneClient
from trpc_agent_sdk.models.systemone import SystemOneError
from trpc_agent_sdk.models.systemone import TypeSafeAPIError

_FIXTURES = Path(__file__).parent / "testdata"
_URL = "https://systemone.test"
_FIXTURE = (_FIXTURES / "jev.json").read_bytes()


def _questions():
    return {
        "route": Choice(instructions="Which team?", criteria={
            "z_billing": "Payments",
            "a_other": None
        }),
        "severity": Score(instructions="Rate urgency", criteria=["low", "medium", "high"]),
        "refund": Noul(instructions="Is a refund requested?"),
    }


def _noul():
    return {"q": Noul(instructions="Is this a greeting?")}


def _ok(status=200, content=None, headers=None):
    return httpx2.Response(status, content=_FIXTURE if content is None else content, headers=headers)


def _noul_body():
    return json.dumps({
        "model": "jev-latest",
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


async def test_fixture_round_trip():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return _ok(headers={"x-typesafe-request-id": "req-123"})

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport, follow_redirects=False) as http:
        client = SystemOneClient.typesafe(
            api_key="test-key",
            base_url=_URL + "/gateway/",
            default_model="default-model",
            http_client=http,
        )
        response = await client.system_one(
            {
                "message": "我被重复扣款了",
                "retries": 2
            },
            _questions(),
            model="explicit-model",
        )
    assert seen["method"] == "POST"
    assert seen["path"] == "/gateway/v1/systemone"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["headers"]["Content-Type"].startswith("application/json")
    assert seen["headers"]["Accept"] == "application/json"
    assert seen["body"]["model"] == "explicit-model"
    assert seen["body"]["state"]["message"] == "我被重复扣款了"
    assert list(seen["body"]["questions"]["route"]["criteria"]) == ["z_billing", "a_other"]
    assert response.request_id == "req-123"
    assert response.raw_http_response.content == _FIXTURE
    assert response.choices["route"].choice == "z_billing"
    assert response.scores["severity"].score == 1.7
    assert response.nouls["refund"].noul == 0


async def test_default_model_is_sent_when_the_call_omits_one():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _ok(content=_noul_body(), headers={"x-typesafe-request-id": "req"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.typesafe(api_key="test-key",
                                          base_url=_URL,
                                          default_model="jev-latest",
                                          http_client=http)
        await client.system_one("hello", _noul())
    assert seen["body"]["model"] == "jev-latest"


async def test_http_error_is_not_retried_unless_asked():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return _ok(429, content=b'{"detail":"nope"}', headers={"x-typesafe-request-id": "failed"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.typesafe(api_key="test-key", base_url=_URL, http_client=http)
        with pytest.raises(TypeSafeRateLimitError) as raised:
            await client.system_one("hello", _noul())
    assert calls == 1
    assert raised.value.status == 429
    assert raised.value.request_id == "failed"

    calls = 0
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.typesafe(
            api_key="test-key",
            base_url=_URL,
            retry=RetryPolicy(max_retries=1, backoff_initial=0, backoff_max=0),
            http_client=http,
        )
        with pytest.raises(TypeSafeRateLimitError):
            await client.system_one("hello", _noul())
    assert calls == 2


async def test_redirect_is_not_followed():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx2.Response(307, headers={"location": _URL + "/secret"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), follow_redirects=False) as http:
        client = SystemOneClient.typesafe(api_key="test-key", base_url=_URL, http_client=http)
        with pytest.raises(TypeSafeAPIError) as raised:
            await client.system_one("hello", _noul())
    assert raised.value.status == 307
    assert calls == ["/v1/systemone"]


async def test_connection_error_is_not_retried():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx2.ConnectError("connection failed")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.typesafe(api_key="test-key", base_url=_URL, http_client=http)
        with pytest.raises(TypeSafeAPIConnectionError) as raised:
            await client.system_one("hello", _noul())
    assert calls == 1
    assert isinstance(raised.value.__cause__, httpx2.ConnectError)


async def test_timeout_and_cancellation():

    async def handler(request):
        await asyncio.sleep(5)
        return _ok(content=_noul_body(), headers={"x-typesafe-request-id": "req"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.typesafe(api_key="test-key", base_url=_URL, timeout=0.05, http_client=http)
        with pytest.raises(TypeSafeAPITimeoutError):
            await client.system_one("hello", _noul())

        client = SystemOneClient.typesafe(api_key="test-key", base_url=_URL, timeout=30, http_client=http)
        task = asyncio.create_task(client.system_one("hello", _noul()))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_external_client_stays_open():
    http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda request: _ok(content=_noul_body(), headers={"x-typesafe-request-id": "req"})),
        timeout=None,
    )
    client = SystemOneClient.typesafe(api_key="test-key", base_url=_URL, http_client=http)
    await client.aclose()
    assert http.is_closed is False
    response = await client.system_one("hello", _noul())
    assert response.nouls["q"].noul == 0
    await http.aclose()


async def test_owned_client_closes_and_does_not_follow_redirects():
    async with SystemOneClient.typesafe(api_key="test-key", base_url=_URL) as client:
        http = client._backend._typesafe_client._http_client
        assert http.follow_redirects is False
        assert http.is_closed is False
    assert http.is_closed is True


async def test_batch_is_rejected():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return _ok(content=_noul_body())

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.typesafe(api_key="test-key", base_url=_URL, http_client=http)
        with pytest.raises(SystemOneError, match="batch is only supported by laya"):
            await client.system_one_batch(["hello"], _noul())
    assert calls == 0
