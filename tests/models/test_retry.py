# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for the model retry execution layer."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Optional
from unittest.mock import AsyncMock
from unittest.mock import patch

from trpc_agent_sdk.configs import ExponentialBackoffConfig
from trpc_agent_sdk.configs import ModelRetryConfig
from trpc_agent_sdk.models._llm_response import LlmResponse
from trpc_agent_sdk.models._retry import _compute_exponential_backoff
from trpc_agent_sdk.models._retry import ModelRetryInfo
from trpc_agent_sdk.models._retry import _model_retry_info_from_exception
from trpc_agent_sdk.models._retry import _should_retry_from_headers_or_status
from trpc_agent_sdk.models._retry import retry_model_call
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class _StatusError(Exception):

    def __init__(self, status_code: int | str, headers: Optional[dict] = None):
        super().__init__(f"status {status_code}")
        self.status_code = status_code
        if headers is not None:
            self.response = type("Resp", (), {"headers": headers})()



class _HeadersError(Exception):

    def __init__(self, headers: dict):
        super().__init__("headers")
        self.headers = headers


class _LiteLlmHeadersError(Exception):

    def __init__(self, headers: dict):
        super().__init__("litellm headers")
        self.litellm_response_headers = headers


class _ResponseWithoutHeaderGet(Exception):

    def __init__(self):
        super().__init__("bad headers")
        self.response = type("Resp", (), {"headers": object()})()


def _is_retriable_status_code(status_code: int) -> bool:
    return status_code in {408, 409, 429} or status_code >= 500


def _retry_info(ex: Exception, should_retry: bool = True) -> ModelRetryInfo:
    return _model_retry_info_from_exception(ex, lambda _: should_retry, lambda _: should_retry)


def _content_response(text: str = "hi", partial: bool = False) -> LlmResponse:
    return LlmResponse(content=Content(parts=[Part.from_text(text=text)], role="model"), partial=partial)


async def _collect(
    call_model,
    config: Optional[ModelRetryConfig] = None,
    *,
    get_retry_info=None,
) -> list[LlmResponse]:
    return [
        response async for response in retry_model_call(
            call_model,
            config,
            get_retry_info=get_retry_info,
        )
    ]


class TestRetryHelpers:

    def test_header_should_retry_has_priority(self):
        assert _should_retry_from_headers_or_status(
            _StatusError(400, {"x-should-retry": "true"}),
            _is_retriable_status_code,
        ) is True
        assert _should_retry_from_headers_or_status(
            _StatusError(500, {"x-should-retry": "false"}),
            _is_retriable_status_code,
        ) is False

    def test_status_retry_decision(self):
        assert _should_retry_from_headers_or_status(_StatusError(408), _is_retriable_status_code) is True
        assert _should_retry_from_headers_or_status(_StatusError(409), _is_retriable_status_code) is True
        assert _should_retry_from_headers_or_status(_StatusError(429), _is_retriable_status_code) is True
        assert _should_retry_from_headers_or_status(_StatusError(500), _is_retriable_status_code) is True
        assert _should_retry_from_headers_or_status(_StatusError(503), _is_retriable_status_code) is True
        assert _should_retry_from_headers_or_status(_StatusError(400), _is_retriable_status_code) is False

    def test_status_retry_decision_uses_model_predicate(self):
        assert _should_retry_from_headers_or_status(_StatusError(499), lambda status_code: status_code == 499) is True
        assert _should_retry_from_headers_or_status(_StatusError(408), lambda status_code: False) is False

    def test_missing_status_has_no_decision(self):
        assert _should_retry_from_headers_or_status(Exception("x"), _is_retriable_status_code) is None

    def test_status_not_applicable_falls_back_to_exception_decision(self):
        retry_info = _model_retry_info_from_exception(
            _StatusError(499),
            lambda _: None,
            lambda _: True,
        )
        assert retry_info.should_retry is True

    def test_retry_after_headers(self):
        assert _retry_info(_StatusError(429, {"retry-after-ms": "2500"}), True).retry_after == 2.5
        assert _retry_info(_StatusError(429, {"retry-after": "7"}), True).retry_after == 7.0
        assert _retry_info(_StatusError(429, {"retry-after": "not-a-date"}), True).retry_after is None
        assert _retry_info(_HeadersError({"retry-after": "3"}), True).retry_after == 3.0
        assert _retry_info(_LiteLlmHeadersError({"retry-after": "4"}), True).retry_after == 4.0
        assert _retry_info(_ResponseWithoutHeaderGet(), True).retry_after is None

    def test_exponential_backoff(self):
        cfg = ExponentialBackoffConfig(jitter=False, initial_backoff=1.0, max_backoff=10.0, multiplier=2.0)
        assert _compute_exponential_backoff(cfg, 0, None) == 1.0
        assert _compute_exponential_backoff(cfg, 1, None) == 2.0
        assert _compute_exponential_backoff(cfg, 10, None) == 10.0

    def test_retry_after_overrides_backoff(self):
        cfg = ExponentialBackoffConfig(jitter=False)
        assert _compute_exponential_backoff(cfg, 0, 5.0) == 5.0
        assert _compute_exponential_backoff(cfg, 0, 60.0) == 60.0

    def test_unreasonable_retry_after_falls_back_to_backoff(self):
        cfg = ExponentialBackoffConfig(jitter=False, initial_backoff=1.0, max_backoff=10.0, multiplier=2.0)
        assert _compute_exponential_backoff(cfg, 0, 0.0) == 1.0
        assert _compute_exponential_backoff(cfg, 0, -1.0) == 1.0
        assert _compute_exponential_backoff(cfg, 0, 61.0) == 1.0

    def test_past_retry_after_date_is_preserved_for_backoff_filter(self):
        with patch("trpc_agent_sdk.models._retry.time.time", return_value=1696004797):
            retry_info = _retry_info(
                _StatusError(429, {"retry-after": "Fri, 29 Sep 2023 16:26:27 GMT"}),
                True,
            )
        assert retry_info.retry_after == -10.0

    def test_jitter_bounds(self):
        cfg = ExponentialBackoffConfig(jitter=True, initial_backoff=1.0, max_backoff=10.0, multiplier=2.0)
        for _ in range(50):
            delay = _compute_exponential_backoff(cfg, 2, None)
            assert 0.0 <= delay <= 4.0


class TestRetryModelCall:

    def _retry_cfg(self, **kw):
        base = dict(
            num_retries=2,
            backoff=ExponentialBackoffConfig(jitter=False, initial_backoff=0.0, max_backoff=0.0),
        )
        base.update(kw)
        return ModelRetryConfig(**base)

    async def test_no_config_converts_exception_without_retry(self):
        attempts = 0

        async def call_model() -> AsyncGenerator[LlmResponse, None]:
            nonlocal attempts
            attempts += 1
            raise _StatusError(429)
            yield

        responses = await _collect(call_model, get_retry_info=lambda _: ModelRetryInfo(should_retry=True))
        assert attempts == 1
        assert responses[0].error_code == "API_ERROR"
        assert responses[0].custom_metadata == {"error": "status 429"}

    async def test_retry_exception_then_success(self):
        attempts = 0

        async def call_model() -> AsyncGenerator[LlmResponse, None]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise _StatusError(429)
            yield _content_response("ok")

        with patch("trpc_agent_sdk.models._retry.asyncio.sleep", new=AsyncMock()) as sleep:
            responses = await _collect(call_model, self._retry_cfg(), get_retry_info=lambda _: ModelRetryInfo(should_retry=True))
        assert attempts == 2
        assert sleep.await_count == 1
        assert responses[-1].content.parts[0].text == "ok"
        assert all(response.error_code is None for response in responses)

    async def test_exhausts_budget_then_yields_error(self):
        attempts = 0

        async def call_model() -> AsyncGenerator[LlmResponse, None]:
            nonlocal attempts
            attempts += 1
            raise _StatusError(500)
            yield

        with patch("trpc_agent_sdk.models._retry.asyncio.sleep", new=AsyncMock()) as sleep:
            responses = await _collect(call_model, self._retry_cfg(num_retries=2), get_retry_info=lambda _: ModelRetryInfo(should_retry=True))
        assert attempts == 3
        assert sleep.await_count == 2
        assert responses[-1].error_code == "API_ERROR"
        assert responses[-1].custom_metadata == {"error": "status 500"}

    async def test_callback_false_not_retried(self):
        attempts = 0

        async def call_model() -> AsyncGenerator[LlmResponse, None]:
            nonlocal attempts
            attempts += 1
            raise _StatusError(400)
            yield

        with patch("trpc_agent_sdk.models._retry.asyncio.sleep", new=AsyncMock()) as sleep:
            responses = await _collect(call_model, self._retry_cfg(), get_retry_info=lambda _: ModelRetryInfo(should_retry=False))
        assert attempts == 1
        assert sleep.await_count == 0
        assert responses[-1].error_code == "API_ERROR"

    async def test_retry_after_callback_controls_delay(self):
        attempts = 0

        async def call_model() -> AsyncGenerator[LlmResponse, None]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise _StatusError(429)
            yield _content_response("ok")

        with patch("trpc_agent_sdk.models._retry.asyncio.sleep", new=AsyncMock()) as sleep:
            responses = await _collect(
                call_model,
                self._retry_cfg(backoff=ExponentialBackoffConfig(jitter=False, initial_backoff=1.0)),
                get_retry_info=lambda _: ModelRetryInfo(should_retry=True, retry_after=3.5),
            )
        sleep.assert_awaited_once_with(3.5)
        assert responses[-1].content.parts[0].text == "ok"

    async def test_no_retry_after_content_emitted(self):
        attempts = 0

        async def call_model() -> AsyncGenerator[LlmResponse, None]:
            nonlocal attempts
            attempts += 1
            yield _content_response("partial", partial=True)
            raise _StatusError(429)

        with patch("trpc_agent_sdk.models._retry.asyncio.sleep", new=AsyncMock()) as sleep:
            responses = await _collect(call_model, self._retry_cfg(), get_retry_info=lambda _: ModelRetryInfo(should_retry=True))
        assert attempts == 1
        assert sleep.await_count == 0
        assert responses[0].content.parts[0].text == "partial"
        assert responses[1].error_code == "API_ERROR"

    async def test_closes_interrupted_attempt_before_retry(self):
        closed_attempts = []

        async def first_attempt() -> AsyncGenerator[LlmResponse, None]:
            try:
                raise _StatusError(429)
                yield
            finally:
                closed_attempts.append("first")

        async def second_attempt() -> AsyncGenerator[LlmResponse, None]:
            yield _content_response("ok")

        attempts = iter([first_attempt, second_attempt])
        with patch("trpc_agent_sdk.models._retry.asyncio.sleep", new=AsyncMock()):
            responses = await _collect(lambda: next(attempts)(), self._retry_cfg(), get_retry_info=lambda _: ModelRetryInfo(should_retry=True))
        assert closed_attempts == ["first"]
        assert responses[-1].content.parts[0].text == "ok"
