# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""HTTP backends for the official TypeSafe API and for Laya."""

from collections.abc import Mapping
from collections.abc import Sequence
from typing import Optional

import httpx2
from typesafe_sdk import AsyncTypeSafeClient
from typesafe_sdk import Question
from typesafe_sdk import RetryPolicy
from typesafe_sdk import SystemOneResponse
from typesafe_sdk import TypeSafeAPIConnectionError
from typesafe_sdk import TypeSafeAPITimeoutError
from typesafe_sdk import TypeSafeError

from ._errors import MAX_RESPONSE_BYTES
from ._errors import SystemOneError
from ._errors import raise_for_status
from ._laya import batch_body
from ._laya import batch_endpoint
from ._laya import decode_batch
from ._laya import decode_response
from ._laya import endpoint
from ._laya import request_body
from ._validate import json_ready_state

_JSON_HEADERS = {"Content-Type": "application/json", "Accept": "application/json"}


class TypeSafeBackend:
    """Call the official API through ``AsyncTypeSafeClient``.

    The official client always sends an API key and a model, and it chooses
    zstd when that encoding is installed. Those choices are what this backend
    keeps. An HTTP client supplied by the caller is not closed here, because
    closing the SDK client closes that client too.
    """

    # The official API has no batch route. ``SystemOneClient.system_one_batch``
    # checks this flag and rejects the call.
    supports_batch = False

    def __init__(
        self,
        *,
        api_key: Optional[str],
        base_url: Optional[str],
        default_model: Optional[str],
        timeout: float,
        retry: Optional[RetryPolicy],
        http_client: Optional[httpx2.AsyncClient],
    ) -> None:
        """Build the official client.

        A missing ``http_client`` is created here and closed by ``aclose``. A
        caller-supplied client stays open. A missing ``retry`` becomes
        ``RetryPolicy(max_retries=0)`` because the SDK retries unless told not to.
        """
        self._owns_http = http_client is None
        if http_client is None:
            http_client = httpx2.AsyncClient(follow_redirects=False, timeout=_http_timeout(timeout))
        self._typesafe_client = AsyncTypeSafeClient(
            api_key=api_key,
            model=default_model,
            base_url=base_url,
            timeout=_sdk_timeout(timeout),
            retry=retry if retry is not None else RetryPolicy(max_retries=0),
            http_client=http_client,
        )

    async def system_one(self, state: object, questions: Mapping[str, Question],
                         model: Optional[str]) -> SystemOneResponse:
        """Evaluate ``questions`` with the official client."""
        return await self._typesafe_client.system_one(state, questions, model=model)

    async def aclose(self) -> None:
        """Close the HTTP client this backend created."""
        if self._owns_http:
            await self._typesafe_client.aclose()


class LayaHttpBackend:
    """Send one plain JSON request to a Laya server.

    An empty API key omits ``Authorization``. An empty model omits ``model``,
    which lets Laya choose the checkpoint. Redirects are not followed and the
    request is not retried. The response is read only up to
    ``MAX_RESPONSE_BYTES`` plus one byte.
    """

    # Laya exposes ``POST /v1/systemone/batch``.
    supports_batch = True

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: Optional[str],
        timeout: float,
        http_client: Optional[httpx2.AsyncClient],
    ) -> None:
        """Store the Laya connection.

        A missing ``http_client`` is created here and closed by ``aclose``. A
        caller-supplied client stays open.
        """
        self._base_url = base_url
        self._api_key = api_key
        self._default_model = default_model
        self._timeout = timeout
        self._owns_http = http_client is None
        self._http = http_client if http_client is not None else httpx2.AsyncClient(
            follow_redirects=False,
            timeout=_http_timeout(timeout),
        )

    async def system_one(self, state: object, questions: Mapping[str, Question],
                         model: Optional[str]) -> SystemOneResponse:
        """Evaluate ``questions`` against one state."""
        url = endpoint(self._base_url)
        body = request_body(json_ready_state(state), questions, _model(model, self._default_model))
        status, received, headers = await self._exchange(url, body)
        return _decode_single(url, status, received, headers)

    async def system_one_batch(
        self,
        states: Sequence[object],
        questions: Mapping[str, Question],
        model: Optional[str],
    ) -> list[SystemOneResponse]:
        """Evaluate the same questions against each state."""
        url = batch_endpoint(self._base_url)
        ready = [json_ready_state(state) for state in states]
        body = batch_body(ready, questions, _model(model, self._default_model))
        status, received, headers = await self._exchange(url, body)
        if status < 200 or status >= 300:
            raise_for_status(status, received, headers, f"POST {url}")
        return decode_batch(url, received, headers, len(states))

    async def aclose(self) -> None:
        """Close the HTTP client this backend created."""
        if self._owns_http:
            await self._http.aclose()

    async def _exchange(self, url: str, body: bytes) -> tuple[int, bytes, httpx2.Headers]:
        """POST ``body`` and return the status, bytes, and headers.

        ``Authorization`` is sent only when an API key is set. Redirects are
        not followed. A timeout or a transport failure becomes the official
        timeout or connection error. ``SystemOneError`` and ``TypeSafeError``
        pass through.
        """
        headers = dict(_JSON_HEADERS)
        if self._api_key:
            headers["Authorization"] = "Bearer " + self._api_key
        try:
            async with self._http.stream(
                    "POST",
                    url,
                    content=body,
                    headers=headers,
                    follow_redirects=False,
                    timeout=_http_timeout(self._timeout),
            ) as response:
                received, read_error = await _read_limited(response)
                return _complete(response, received, read_error, url)
        except (SystemOneError, TypeSafeError):
            raise
        except httpx2.TimeoutException as exc:
            raise TypeSafeAPITimeoutError(self._timeout) from exc
        except Exception as exc:
            raise TypeSafeAPIConnectionError("systemone: send request") from exc


def _decode_single(url: str, status: int, body: bytes, headers: httpx2.Headers) -> SystemOneResponse:
    """Turn one HTTP result into a Laya response.

    A non-2xx status becomes the official API error for that status.
    """
    if status < 200 or status >= 300:
        raise_for_status(status, body, headers, f"POST {url}")
    return decode_response(url, body, headers)


def _complete(response: httpx2.Response, body: bytes, read_error: Optional[Exception],
              url: str) -> tuple[int, bytes, httpx2.Headers]:
    """Finish a streamed response.

    A failed status is reported even when the read also failed, and the read
    error is the cause. A successful body longer than ``MAX_RESPONSE_BYTES``
    is rejected. Headers are copied so they stay usable after the stream closes.
    """
    failed = response.status_code < 200 or response.status_code >= 300
    if failed and read_error is not None:
        try:
            raise_for_status(response.status_code, body, response.headers, f"POST {url}")
        except TypeSafeError as exc:
            raise exc from read_error
    if read_error is not None:
        raise SystemOneError("systemone: read response") from read_error
    if not failed and len(body) > MAX_RESPONSE_BYTES:
        raise SystemOneError(f"systemone: response exceeds {MAX_RESPONSE_BYTES} bytes")
    return response.status_code, body, httpx2.Headers(response.headers)


async def _read_limited(response: httpx2.Response) -> tuple[bytes, Optional[Exception]]:
    """Read at most ``MAX_RESPONSE_BYTES + 1`` bytes.

    The extra byte distinguishes a full accepted body from one that was cut
    off. A read error is returned with the bytes already received, so a
    non-2xx body can still become an API error.
    """
    limit = MAX_RESPONSE_BYTES + 1
    parts: list[bytes] = []
    size = 0
    try:
        async for chunk in response.aiter_bytes():
            room = limit - size
            if len(chunk) > room:
                parts.append(chunk[:room])
                size = limit
                break
            parts.append(chunk)
            size += len(chunk)
            if size >= limit:
                break
    except Exception as exc:
        return b"".join(parts), exc
    return b"".join(parts), None


def _model(model: Optional[str], default_model: Optional[str]) -> Optional[str]:
    """Choose the model to send.

    A per-call ``model`` replaces ``default_model``. ``None`` and ``""`` both
    become ``None``, so Laya omits ``model`` and picks the checkpoint. A
    non-string raises ``SystemOneError``.
    """
    chosen = default_model if model is None else model
    if chosen is None:
        return None
    if not isinstance(chosen, str):
        raise SystemOneError("systemone: model must be a string")
    return chosen or None


def _http_timeout(timeout: float) -> httpx2.Timeout | float:
    """Return the HTTP timeout for ``timeout`` seconds.

    ``0`` and any negative value become ``httpx2.Timeout(None)``, which means
    the HTTP layer has no deadline. A positive value is passed through.
    """
    if timeout <= 0:
        return httpx2.Timeout(None)
    return timeout


def _sdk_timeout(timeout: float) -> httpx2.Timeout | float:
    """Return a timeout the official client accepts.

    The SDK rejects ``0``. No client deadline is expressed as an unlimited
    HTTP timeout, and ``SystemOneClient`` does not add its own wait.
    """
    return _http_timeout(timeout)
