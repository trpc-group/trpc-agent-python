# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Errors raised by the System One client."""

import json
from typing import NoReturn

import httpx2
from typesafe_sdk import TypeSafeAPIError
from typesafe_sdk import TypeSafeAuthenticationError
from typesafe_sdk import TypeSafeBadRequestError
from typesafe_sdk import TypeSafeError
from typesafe_sdk import TypeSafeInternalServerError
from typesafe_sdk import TypeSafeNotFoundError
from typesafe_sdk import TypeSafePermissionDeniedError
from typesafe_sdk import TypeSafeRateLimitError
from typesafe_sdk import TypeSafeUnprocessableEntityError

# One byte past the limit distinguishes an accepted body from a rejected one.
MAX_RESPONSE_BYTES = 4 << 20

_STATUS_ERRORS = {
    400: TypeSafeBadRequestError,
    401: TypeSafeAuthenticationError,
    403: TypeSafePermissionDeniedError,
    404: TypeSafeNotFoundError,
    422: TypeSafeUnprocessableEntityError,
    429: TypeSafeRateLimitError,
}


class SystemOneError(TypeSafeError):
    """A System One call failed before a usable response was available.

    Invalid arguments, response limits, and answer mismatches use this type.
    HTTP failures use the official ``TypeSafeAPIError`` subclasses instead, so
    ``except TypeSafeError`` catches every failure from either backend.
    """


def raise_for_status(status: int, body: bytes, headers: httpx2.Headers, endpoint: str) -> NoReturn:
    """Raise the official API error for a non-2xx response.

    ``str(error)`` contains the status and endpoint only. The response body
    stays on ``error.body`` and is capped at ``MAX_RESPONSE_BYTES``.
    """
    if len(body) > MAX_RESPONSE_BYTES:
        body = body[:MAX_RESPONSE_BYTES]
    if not body:
        parsed: object = None
    else:
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = body.decode("utf-8", errors="replace")
    if status >= 500:
        error_type = TypeSafeInternalServerError
    else:
        error_type = _STATUS_ERRORS.get(status, TypeSafeAPIError)
    raise error_type(status, parsed, headers, message=f"http status {status}", endpoint=endpoint)
