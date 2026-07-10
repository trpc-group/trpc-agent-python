# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Provider-agnostic model retry execution utilities."""

from __future__ import annotations

import asyncio
import email.utils
import random
import time
from collections.abc import AsyncGenerator
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from typing import Optional

from trpc_agent_sdk.configs import ExponentialBackoffConfig
from trpc_agent_sdk.configs import ModelRetryConfig
from trpc_agent_sdk.log import logger

from ._llm_response import LlmResponse

_MAX_RETRY_AFTER_SECONDS = 60.0


@dataclass(frozen=True)
class ModelRetryInfo:
    """Provider retry decision and optional server-suggested delay.

    should_retry indicates whether the failed model call is safe to retry.
    retry_after is the HTTP-standard Retry-After value, expressed in seconds.
    When the server provides it, SDK-managed retry should respect this delay.
    """

    should_retry: bool
    retry_after: Optional[float] = None


def _extract_status_code(ex: Exception) -> Optional[int]:
    status_code = getattr(ex, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    if isinstance(status_code, str):
        try:
            return int(status_code)
        except ValueError:
            return None

    return None


def _extract_headers(ex: Exception) -> Any:
    # LiteLLM attaches headers to normalized exceptions.
    litellm_headers = getattr(ex, "litellm_response_headers", None)
    if litellm_headers is not None:
        return litellm_headers

    response = getattr(ex, "response", None)
    response_headers = getattr(response, "headers", None)
    if response_headers is not None:
        return response_headers

    return getattr(ex, "headers", None)


def _get_header(headers: Any, name: str) -> Any:
    get_header = getattr(headers, "get", None)
    if get_header is None:
        return None
    value = get_header(name)
    if value is not None:
        return value
    return get_header(name.lower())


def _retry_after_from_headers(headers: Any) -> Optional[float]:
    retry_after_ms = _get_header(headers, "retry-after-ms")
    if retry_after_ms is not None:
        try:
            return float(retry_after_ms) / 1000.0
        except (TypeError, ValueError):
            pass

    retry_after = _get_header(headers, "retry-after")
    if retry_after is None:
        return None
    try:
        return float(retry_after)
    except (TypeError, ValueError):
        parsed = email.utils.parsedate_tz(retry_after)
        if parsed is None:
            return None
        return email.utils.mktime_tz(parsed) - time.time()


def _should_retry_from_headers_or_status(
    ex: Exception,
    is_retriable_status_code: Callable[[int], Optional[bool]],
) -> Optional[bool]:
    headers = _extract_headers(ex)
    # x-should-retry is not a standard header, but it's supported by both the OpenAI and Anthropic api.
    should_retry = _get_header(headers, "x-should-retry")
    if isinstance(should_retry, str):
        normalized = should_retry.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False

    status_code = _extract_status_code(ex)
    if status_code is None:
        return None
    return is_retriable_status_code(status_code)


def _model_retry_info_from_exception(
    ex: Exception,
    is_retriable_status_code: Callable[[int], Optional[bool]],
    is_retriable_exception: Callable[[Exception], bool],
) -> ModelRetryInfo:
    decision = _should_retry_from_headers_or_status(ex, is_retriable_status_code)
    if decision is None:
        decision = is_retriable_exception(ex)
    return ModelRetryInfo(should_retry=decision, retry_after=_retry_after_from_headers(_extract_headers(ex)))


def _compute_exponential_backoff(
    config: ExponentialBackoffConfig,
    attempt: int,
    retry_after: Optional[float],
) -> float:
    if retry_after is not None and 0 < retry_after <= _MAX_RETRY_AFTER_SECONDS:
        return float(retry_after)

    delay = config.initial_backoff * (config.multiplier**attempt)
    delay = min(delay, config.max_backoff)
    if config.jitter:
        return random.uniform(0.0, delay)
    return delay


def _build_error_response(ex: Exception, error_code: str) -> LlmResponse:
    logger.error("Model call failed: %s", ex, exc_info=True)
    return LlmResponse(
        content=None,
        error_code=error_code,
        error_message=str(ex),
        custom_metadata={"error": str(ex)},
    )


async def retry_model_call(
    call_model: Callable[[], AsyncGenerator[LlmResponse, None]],
    retry_config: Optional[ModelRetryConfig],
    *,
    error_code: str = "API_ERROR",
    get_retry_info: Callable[[Exception], ModelRetryInfo] | None = None,
) -> AsyncGenerator[LlmResponse, None]:
    """Execute a model call with SDK-managed retry.

    Retries only when an attempt raises before emitting user-visible content. Once
    content has been yielded, subsequent failures are converted to a final error
    response and surfaced without replaying the request.
    """
    attempt = 0

    while True:
        produced_content = False
        attempt_stream = call_model()
        try:
            async for response in attempt_stream:
                if response.has_content():
                    produced_content = True
                yield response
            return
        except Exception as ex:  # pylint: disable=broad-except
            if retry_config is None or produced_content:
                yield _build_error_response(ex, error_code)
                return

            if attempt >= retry_config.num_retries:
                yield _build_error_response(ex, error_code)
                return

            if get_retry_info is None:
                yield _build_error_response(ex, error_code)
                return

            retry_info = get_retry_info(ex)
            if not retry_info.should_retry:
                yield _build_error_response(ex, error_code)
                return

            delay = _compute_exponential_backoff(retry_config.backoff, attempt, retry_info.retry_after)
            logger.warning(
                "Model call failed (exception=%s); retrying in %.2fs (attempt %d/%d).",
                type(ex).__name__,
                delay,
                attempt + 1,
                retry_config.num_retries,
            )
            await asyncio.sleep(delay)
            attempt += 1
        finally:
            await attempt_stream.aclose()
