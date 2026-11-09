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
from typesafe_sdk import TypeSafeAPIError
from typesafe_sdk import TypeSafeAPITimeoutError
from typesafe_sdk import TypeSafeInternalServerError
from typesafe_sdk import TypeSafeRateLimitError

from trpc_agent_sdk.models.systemone import Choice
from trpc_agent_sdk.models.systemone import Noul
from trpc_agent_sdk.models.systemone import Score
from trpc_agent_sdk.models.systemone import SystemOneClient
from trpc_agent_sdk.models.systemone import SystemOneError
from trpc_agent_sdk.models.systemone import noul_answer
from trpc_agent_sdk.models.systemone._errors import MAX_RESPONSE_BYTES

_FIXTURES = Path(__file__).parent / "testdata"
_URL = "https://systemone.test"
_FIXTURE = (_FIXTURES / "laya.json").read_bytes()


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


def _noul_body(noul=0):
    return json.dumps({
        "model": "english",
        "answers": {
            "q": {
                "type": "noul",
                "noul": noul
            }
        },
        "usage": {
            "input_tokens": 1,
            "output_tokens": 0
        },
        "routing": {
            "model": "english",
            "repo": "example/english"
        },
    }).encode()


def _ok(status=200, content=None, headers=None):
    return httpx2.Response(status, content=_noul_body() if content is None else content, headers=headers)


async def test_fixture_round_trip():
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, content=_FIXTURE, headers={"X-Request-ID": "req-123"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.laya(_URL + "/gateway/", api_key="test-key", default_model="english", http_client=http)
        response = await client.system_one(
            {"message": "我被重复扣款了"},
            _questions(),
            model="explicit-model",
        )
    assert seen["method"] == "POST"
    assert seen["path"] == "/gateway/v1/systemone"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["headers"]["Content-Type"] == "application/json"
    assert seen["headers"].get("Content-Encoding") is None
    assert seen["body"]["model"] == "explicit-model"
    assert list(seen["body"]["questions"]["route"]["criteria"]) == ["z_billing", "a_other"]
    assert response.request_id == "req-123"
    assert response.raw_http_response.content == _FIXTURE
    assert response.routing.model == "multilingual"
    assert response.choices["route"].choice == "z_billing"
    assert response.scores["severity"].score == 1.7
    assert response.nouls["refund"].noul == 0


async def test_omits_key_and_model():
    seen = {}

    def handler(request):
        seen["authorization"] = request.headers.get("Authorization")
        seen["encoding"] = request.headers.get("Content-Encoding")
        seen["body"] = json.loads(request.content)
        return _ok(headers={"X-Request-ID": "req"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.laya(_URL, http_client=http)
        response = await client.system_one("hello", _noul())
    assert seen["authorization"] is None
    assert seen["encoding"] is None
    assert "model" not in seen["body"]
    assert noul_answer(response, "q").noul == 0


async def test_errors_are_not_retried_and_hide_the_body():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _ok(429, content=b'{"detail":"sensitive error data"}', headers={"X-Request-ID": "failed"})
        return _ok(503, content=b'{"detail":"sensitive server data"}', headers={"X-Request-ID": "down"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.laya(_URL, http_client=http)
        with pytest.raises(TypeSafeRateLimitError) as limited:
            await client.system_one("hello", _noul())
        with pytest.raises(TypeSafeInternalServerError) as failed:
            await client.system_one("hello", _noul())
    assert calls == 2
    assert limited.value.status == 429
    assert failed.value.status == 503
    assert "sensitive" not in str(limited.value)
    assert "sensitive" not in str(failed.value)


async def test_redirect_is_not_followed():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/systemone":
            return httpx2.Response(307, headers={"location": _URL + "/secret"})
        return _ok()

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler), follow_redirects=True) as http:
        client = SystemOneClient.laya(_URL, http_client=http)
        with pytest.raises(TypeSafeAPIError) as raised:
            await client.system_one("hello", _noul())
    assert raised.value.status == 307
    assert calls == ["/v1/systemone"]


async def test_response_size_limit():

    def oversized(request):
        return _ok(content=b" " * (MAX_RESPONSE_BYTES + 1))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(oversized)) as http:
        client = SystemOneClient.laya(_URL, http_client=http)
        with pytest.raises(SystemOneError, match="response exceeds") as raised:
            await client.system_one("hello", _noul())
    assert not isinstance(raised.value, TypeSafeAPIError)


async def test_error_body_is_capped():

    def handler(request):
        return _ok(503, content=b"y" * (MAX_RESPONSE_BYTES + 1), headers={"X-Request-ID": "too-big"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.laya(_URL, http_client=http)
        with pytest.raises(TypeSafeInternalServerError) as raised:
            await client.system_one("hello", _noul())
    body = raised.value.body
    assert isinstance(body, str)
    assert len(body) == MAX_RESPONSE_BYTES
    assert "y" * 8 not in str(raised.value)


async def test_connection_error_is_not_retried():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx2.ConnectError("connection failed")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.laya(_URL, http_client=http)
        with pytest.raises(TypeSafeAPIConnectionError) as raised:
            await client.system_one("hello", _noul())
    assert calls == 1
    assert isinstance(raised.value.__cause__, httpx2.ConnectError)


async def test_timeout_and_cancellation():

    async def handler(request):
        await asyncio.sleep(5)
        return _ok(headers={"X-Request-ID": "req"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SystemOneClient.laya(_URL, timeout=0.05, http_client=http)
        with pytest.raises(TypeSafeAPITimeoutError):
            await client.system_one("hello", _noul())

        client = SystemOneClient.laya(_URL, timeout=30, http_client=http)
        task = asyncio.create_task(client.system_one("hello", _noul()))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_external_client_stays_open():
    http = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda request: _ok(headers={"X-Request-ID": "req"})),
        timeout=None,
    )
    client = SystemOneClient.laya(_URL, http_client=http)
    await client.aclose()
    assert http.is_closed is False
    response = await client.system_one("hello", _noul())
    assert noul_answer(response, "q").noul == 0
    await http.aclose()


async def test_owned_client_closes():
    async with SystemOneClient.laya(_URL) as client:
        http = client._backend._http
        assert http.is_closed is False
    assert http.is_closed is True


async def test_batch_round_trip_and_local_limits():
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        item = json.loads(_noul_body(0.4))
        return httpx2.Response(
            200,
            json={
                "results": [item, item],
                "total_usage": {
                    "input_tokens": 2,
                    "output_tokens": 0
                }
            },
            headers={"X-Request-ID": "batch-1"},
        )

    calls = 0

    def counting(request):
        nonlocal calls
        calls += 1
        return handler(request)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(counting)) as http:
        client = SystemOneClient.laya(_URL, http_client=http)
        with pytest.raises(SystemOneError, match="between 1 and 64"):
            await client.system_one_batch([], _noul())
        with pytest.raises(SystemOneError, match="between 1 and 64"):
            await client.system_one_batch(["x"] * 65, _noul())
        assert calls == 0
        responses = await client.system_one_batch(["one", "two"], _noul())
    assert seen["path"] == "/v1/systemone/batch"
    assert seen["body"]["states"] == ["one", "two"]
    assert "model" not in seen["body"]
    assert len(responses) == 2
    assert [item.routing.model for item in responses] == ["english", "english"]
    assert [noul_answer(item, "q").noul for item in responses] == [0.4, 0.4]
    assert responses[0].request_id == "batch-1"
