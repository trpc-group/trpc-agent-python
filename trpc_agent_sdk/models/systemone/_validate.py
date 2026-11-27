# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Validate System One arguments and check answers against the questions that were asked."""

import re
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel
from typesafe_sdk import Box
from typesafe_sdk import BoxAnswer
from typesafe_sdk import Choice
from typesafe_sdk import ChoiceAnswer
from typesafe_sdk import Noul
from typesafe_sdk import NoulAnswer
from typesafe_sdk import Question
from typesafe_sdk import Score
from typesafe_sdk import ScoreAnswer
from typesafe_sdk import SystemOneResponse

from ._errors import SystemOneError

_INVALID_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ANSWER_TYPES = {
    "noul": NoulAnswer,
    "choice": ChoiceAnswer,
    "score": ScoreAnswer,
    "box": BoxAnswer,
}
_QUESTION_TYPES = (Noul, Choice, Score, Box)


def validate_timeout(timeout: float) -> float:
    """Reject a negative timeout or a non-numeric value.

    ``0`` is accepted and means the call has no deadline. ``bool`` is rejected
    because it is an ``int`` subclass.
    """
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout < 0:
        raise SystemOneError("systemone: timeout must not be negative")
    return float(timeout)


def validate_base_url(base_url: str) -> str:
    """Return ``base_url`` without a trailing slash, or raise ``SystemOneError``.

    The URL must be http or https, must name a host, and must not carry user
    info, a query, or a fragment. A path prefix is preserved.
    """
    if not isinstance(base_url, str) or not base_url or any(char in base_url for char in "?#"):
        raise SystemOneError("systemone: invalid base url")
    if _INVALID_PERCENT.search(base_url):
        raise SystemOneError("systemone: invalid base url")
    try:
        parts = urlsplit(base_url)
    except ValueError as exc:
        raise SystemOneError("systemone: invalid base url") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise SystemOneError("systemone: invalid base url")
    if parts.username is not None or parts.password is not None:
        raise SystemOneError("systemone: invalid base url")
    return parts.geturl().rstrip("/")


def validate_api_key(api_key: Optional[str]) -> str:
    """Return the key to send, or ``""`` when the request is anonymous.

    A Laya server without ``LAYA_API_KEY`` accepts the empty result. A key that
    contains a line break is rejected so it cannot inject a header.
    """
    if api_key is None or api_key == "":
        return ""
    if not isinstance(api_key, str) or "\r" in api_key or "\n" in api_key:
        raise SystemOneError("systemone: invalid api key")
    return api_key


def validate_state(state: object) -> None:
    """Reject a missing state before any request is sent."""
    if state is None:
        raise SystemOneError("systemone: state is required")


def validate_questions(questions: Mapping[str, Question]) -> None:
    """Reject an empty question map, a blank id, or a question with no criteria."""
    if not isinstance(questions, Mapping) or not questions:
        raise SystemOneError("systemone: at least one question is required")
    for name, question in questions.items():
        if not isinstance(name, str) or name.strip() == "":
            raise SystemOneError("systemone: question id must not be blank")
        _validate_question(name, question)


def check_answers(questions: Mapping[str, Question], response: SystemOneResponse) -> None:
    """Raise ``SystemOneError`` when ``response`` does not answer ``questions``.

    Every question needs an answer of the same type. A choice answer must pick
    a requested label, and its probability keys must be requested labels. A
    score answer must report one probability per requested level.
    """
    for name, question in questions.items():
        kind = _question_kind(name, question)
        expected = _ANSWER_TYPES.get(kind)
        if expected is None:
            raise SystemOneError(f"systemone: question {name!r}: unknown type {kind!r}")
        answer = response.answers.get(name) if isinstance(response.answers, dict) else None
        if answer is None:
            raise SystemOneError(f'systemone: answer "{name}" not found')
        if not isinstance(answer, expected):
            raise SystemOneError(
                f'systemone: answer "{name}": expected {kind}, got {getattr(answer, "type", type(answer).__name__)}')
        if isinstance(answer, ChoiceAnswer):
            _check_choice(name, _choice_labels(name, question), answer)
        elif isinstance(answer, ScoreAnswer):
            _check_score(name, _score_levels(name, question), answer)


def _validate_question(name: str, question: Question) -> None:
    if isinstance(question, _QUESTION_TYPES):
        _reject_blank_text(name, getattr(question, "instructions", None))
        if isinstance(question, Choice) and not question.criteria:
            raise SystemOneError(f"systemone: question {name!r}: criteria must not be empty")
        if isinstance(question, Score) and not question.criteria:
            raise SystemOneError(f"systemone: question {name!r}: criteria must not be empty")
        return
    if not isinstance(question, dict) or not isinstance(question.get("type"), str) or not question["type"]:
        raise SystemOneError(f"systemone: question {name!r}: expected a question object or a typed dictionary")
    _reject_blank_text(name, question.get("instructions"))
    kind = question["type"]
    if kind == "choice" and not _mapping(question.get("criteria")):
        raise SystemOneError(f"systemone: question {name!r}: criteria must not be empty")
    if kind == "score" and not _sequence(question.get("criteria")):
        raise SystemOneError(f"systemone: question {name!r}: criteria must not be empty")


def _reject_blank_text(name: str, instructions: object) -> None:
    if isinstance(instructions, str) and instructions.strip() == "":
        raise SystemOneError(f"systemone: question {name!r}: instructions must not be blank")


def _question_kind(name: str, question: Question) -> str:
    if isinstance(question, _QUESTION_TYPES):
        return question.type
    if isinstance(question, dict) and isinstance(question.get("type"), str):
        return question["type"]
    raise SystemOneError(f"systemone: question {name!r}: expected a question object or a typed dictionary")


def _choice_labels(name: str, question: Question) -> set[str]:
    criteria = question.criteria if isinstance(
        question, Choice) else question.get("criteria") if isinstance(question, dict) else None
    if not isinstance(criteria, Mapping):
        raise SystemOneError(f"systemone: question {name!r}: criteria must not be empty")
    return {label for label in criteria if isinstance(label, str)}


def _score_levels(name: str, question: Question) -> int:
    criteria = question.criteria if isinstance(
        question, Score) else question.get("criteria") if isinstance(question, dict) else None
    if not _sequence(criteria):
        raise SystemOneError(f"systemone: question {name!r}: criteria must not be empty")
    return len(criteria)


def _check_choice(name: str, labels: set[str], answer: ChoiceAnswer) -> None:
    if answer.choice not in labels:
        raise SystemOneError(f'systemone: answer "{name}": choice {answer.choice!r} is not in criteria')
    for label in answer.probabilities:
        if label not in labels:
            raise SystemOneError(f'systemone: answer "{name}": probability {label!r} is not in criteria')


def _check_score(name: str, levels: int, answer: ScoreAnswer) -> None:
    if len(answer.probabilities) != levels:
        raise SystemOneError(
            f'systemone: answer "{name}": expected {levels} score levels, got {len(answer.probabilities)}')


def _mapping(value: object) -> bool:
    return isinstance(value, Mapping) and len(value) > 0


def _sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) > 0


def json_ready_state(state: object) -> object:
    """Return ``state`` in a form ``json.dumps`` can encode."""
    if isinstance(state, BaseModel):
        return state.model_dump(mode="json")
    return state
