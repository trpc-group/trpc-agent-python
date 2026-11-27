# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""System One client for the official TypeSafe API and for Laya HTTP."""

import asyncio
from collections.abc import Awaitable
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Optional

import httpx2
from typesafe_sdk import Question
from typesafe_sdk import RetryPolicy
from typesafe_sdk import SystemOneResponse
from typesafe_sdk import TypeSafeAPITimeoutError

from ._backends import LayaHttpBackend
from ._backends import TypeSafeBackend
from ._errors import SystemOneError
from ._validate import check_answers
from ._validate import validate_api_key
from ._validate import validate_base_url
from ._validate import validate_questions
from ._validate import validate_state
from ._validate import validate_timeout

# Seconds used when the caller omits timeout. ``0`` means the call has no deadline.
_DEFAULT_TIMEOUT = 30.0
# Laya accepts 1 to 64 states per batch. The error text uses this same limit.
_MAX_BATCH_STATES = 64


class SystemOneClient:
    """Evaluate System One questions through TypeSafe or Laya.

    ``typesafe`` talks to the official API. It requires an API key and always
    sends a model, because the official SDK requires both. ``laya`` talks to
    ``laya-serve``. It omits ``Authorization`` when no key is set and omits
    ``model`` when none is set, so Laya can choose the checkpoint.

    Both backends return the official response type. Laya responses are a
    subclass that also carries ``routing``. ``system_one_batch`` is only
    available on the Laya backend.

    A timeout of ``0`` disables the deadline. A negative timeout is rejected.
    The client does not follow redirects. The official backend does not retry
    unless a ``RetryPolicy`` is supplied. The Laya backend never retries.
    """

    def __init__(self, backend: TypeSafeBackend | LayaHttpBackend, timeout: float) -> None:
        self._backend = backend
        self._timeout = timeout

    @classmethod
    def typesafe(
        cls,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        retry: Optional[RetryPolicy] = None,
        http_client: Optional[httpx2.AsyncClient] = None,
    ) -> "SystemOneClient":
        """Create a client for the official TypeSafe API.

        ``api_key``, ``base_url``, and ``default_model`` fall back to
        ``TYPESAFE_API_KEY``, ``TYPESAFE_BASE_URL``, and
        ``TYPESAFE_DEFAULT_MODEL`` when omitted. The official default model is
        ``jev-latest``.
        """
        timeout = validate_timeout(timeout)
        if base_url is not None:
            base_url = validate_base_url(base_url)
        _require_async_client(http_client)
        backend = TypeSafeBackend(
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
            timeout=timeout,
            retry=retry,
            http_client=http_client,
        )
        return cls(backend, timeout)

    @classmethod
    def laya(
        cls,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        http_client: Optional[httpx2.AsyncClient] = None,
    ) -> "SystemOneClient":
        """Create a client for a Laya HTTP server.

        ``base_url`` is the server root, for example ``http://127.0.0.1:8000``.
        A path prefix on that root is preserved.
        """
        timeout = validate_timeout(timeout)
        _require_async_client(http_client)
        backend = LayaHttpBackend(
            base_url=validate_base_url(base_url),
            api_key=validate_api_key(api_key),
            default_model=default_model,
            timeout=timeout,
            http_client=http_client,
        )
        return cls(backend, timeout)

    async def system_one(
        self,
        state: object,
        questions: Mapping[str, Question],
        *,
        model: Optional[str] = None,
    ) -> SystemOneResponse:
        """Evaluate ``questions`` against ``state``.

        The questions are checked before the request is sent. The response is
        rejected when an answer is missing, has the wrong type, selects an
        unknown choice, or reports a different number of score levels.
        """
        validate_state(state)
        validate_questions(questions)

        async def run() -> SystemOneResponse:
            response = await self._backend.system_one(state, questions, model)
            check_answers(questions, response)
            return response

        return await self._wait(run())

    async def system_one_batch(
        self,
        states: Sequence[object],
        questions: Mapping[str, Question],
        *,
        model: Optional[str] = None,
    ) -> list[SystemOneResponse]:
        """Evaluate ``questions`` against each state on a Laya server.

        The batch contains 1 to 64 states. The official backend raises
        ``SystemOneError``.
        """
        validate_questions(questions)
        _validate_states(states)
        if not self._backend.supports_batch:
            raise SystemOneError("systemone: batch is only supported by laya")

        async def run() -> list[SystemOneResponse]:
            responses = await self._backend.system_one_batch(states, questions, model)
            for response in responses:
                check_answers(questions, response)
            return responses

        return await self._wait(run())

    async def aclose(self) -> None:
        """Close the HTTP client created by this object.

        A client passed as ``http_client`` stays open.
        """
        await self._backend.aclose()

    async def __aenter__(self) -> "SystemOneClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def _wait(self, awaitable: Awaitable[object]) -> object:
        if self._timeout <= 0:
            return await awaitable
        try:
            return await asyncio.wait_for(awaitable, self._timeout)
        except TypeSafeAPITimeoutError:
            raise
        except TimeoutError as exc:
            raise TypeSafeAPITimeoutError(self._timeout) from exc


def _require_async_client(http_client: Optional[httpx2.AsyncClient]) -> None:
    if http_client is not None and not isinstance(http_client, httpx2.AsyncClient):
        raise SystemOneError("systemone: http client must be an httpx2.AsyncClient")


def _validate_states(states: Sequence[object]) -> None:
    if isinstance(states, (str, bytes)) or not isinstance(states, Sequence):
        raise SystemOneError("systemone: batch accepts between 1 and 64 states")
    if not 1 <= len(states) <= _MAX_BATCH_STATES:
        raise SystemOneError("systemone: batch accepts between 1 and 64 states")
    for state in states:
        validate_state(state)
