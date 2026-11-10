# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Laya response fields and batch decoding.

Laya's ``POST /v1/systemone`` body matches the official System One response
and adds a ``routing`` object. ``POST /v1/systemone/batch`` returns those
responses under ``results``, plus ``total_usage`` for the whole call.
"""

import json
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Optional

import httpx2
from pydantic import BaseModel
from typesafe_sdk import Question
from typesafe_sdk import SystemOneResponse
from typesafe_sdk._core.schemas.base import Schema

from ._errors import SystemOneError

_BATCH_ROUTE = "/v1/systemone/batch"
_SYSTEM_ONE_ROUTE = "/v1/systemone"


class LayaRouting(Schema):
    """The checkpoint Laya selected for a request."""

    model: str
    """Checkpoint name, such as ``english`` or ``multilingual``."""
    repo: Optional[str] = None
    """Hugging Face repository that supplied the checkpoint, when reported."""


class LayaSystemOneResponse(SystemOneResponse):
    """A System One response plus Laya's routing report.

    Fields the official model does not declare, including per-answer ``action``
    and ``usage.truncated``, stay in ``raw_http_response``.
    """

    routing: Optional[LayaRouting] = None
    """Checkpoint selection. ``None`` when the server omitted it."""


def encode_questions(questions: Mapping[str, Question]) -> dict[str, object]:
    """Serialize question objects to the JSON object Laya expects."""
    encoded: dict[str, object] = {}
    for name, question in questions.items():
        if isinstance(question, BaseModel):
            encoded[name] = question.model_dump(mode="json", exclude_none=True)
        elif isinstance(question, dict):
            encoded[name] = question
        else:
            raise SystemOneError(f"systemone: question {name!r}: expected a question object or a typed dictionary")
    return encoded


def request_body(state: object, questions: Mapping[str, Question], model: Optional[str]) -> bytes:
    """Encode one System One request, omitting ``model`` when it is empty."""
    return _dumps(_payload(questions, model, state=state))


def batch_body(states: Sequence[object], questions: Mapping[str, Question], model: Optional[str]) -> bytes:
    """Encode a Laya batch request."""
    return _dumps(_payload(questions, model, states=list(states)))


def endpoint(base_url: str, route: str = _SYSTEM_ONE_ROUTE) -> str:
    """Join a normalized API root with a System One route."""
    return base_url.rstrip("/") + route


def batch_endpoint(base_url: str) -> str:
    """Return the Laya batch route for ``base_url``."""
    return endpoint(base_url, _BATCH_ROUTE)


def decode_response(url: str, body: bytes, headers: httpx2.Headers) -> LayaSystemOneResponse:
    """Parse one successful Laya response and keep its ``X-Request-ID``."""
    parsed = _parse_item(url, body, headers)
    request_id = headers.get("X-Request-ID")
    if request_id is not None:
        parsed.__dict__["_request_id"] = request_id
    return parsed


def decode_batch(url: str, body: bytes, headers: httpx2.Headers, expected: int) -> list[LayaSystemOneResponse]:
    """Split a batch response into one checked response per submitted state."""
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise SystemOneError("systemone: batch response is not JSON") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list):
        raise SystemOneError("systemone: batch response is missing results")
    if len(results) != expected:
        raise SystemOneError("systemone: batch response size does not match states")
    decoded = []
    for item in results:
        if not isinstance(item, dict):
            raise SystemOneError("systemone: batch response item is not an object")
        decoded.append(decode_response(url, json.dumps(item, ensure_ascii=False).encode("utf-8"), headers))
    return decoded


def _payload(questions: Mapping[str, Question], model: Optional[str], **fields: object) -> dict[str, object]:
    """Build the JSON object for one call or a batch.

    ``model`` is omitted when it is empty, so Laya can choose the checkpoint.
    ``fields`` is either ``state`` or ``states``.
    """
    body: dict[str, object] = dict(fields)
    if model:
        body["model"] = model
    body["questions"] = encode_questions(questions)
    return body


def _dumps(body: dict[str, object]) -> bytes:
    """Encode ``body`` as UTF-8 JSON.

    NaN and Infinity are rejected. A value ``json.dumps`` cannot encode becomes
    ``SystemOneError``.
    """
    try:
        return json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SystemOneError("systemone: state must be JSON") from exc


def _parse_item(url: str, body: bytes, headers: httpx2.Headers) -> LayaSystemOneResponse:
    """Parse ``body`` with the official response model.

    The bytes are wrapped in an ``httpx2.Response`` because
    ``LayaSystemOneResponse.from_http_response`` reads the body and headers
    from that object. ``url`` is only the synthetic request URL.
    """
    response = httpx2.Response(
        200,
        content=body,
        headers=headers,
        request=httpx2.Request("POST", url),
    )
    return LayaSystemOneResponse.from_http_response(response)
