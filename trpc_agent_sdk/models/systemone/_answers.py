# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Look up one typed answer by the question name sent in the request."""

from typing import TypeVar

from typesafe_sdk import ChoiceAnswer
from typesafe_sdk import NoulAnswer
from typesafe_sdk import ScoreAnswer
from typesafe_sdk import SystemOneResponse

from ._errors import SystemOneError

_AnswerT = TypeVar("_AnswerT", NoulAnswer, ChoiceAnswer, ScoreAnswer)


def noul_answer(response: SystemOneResponse, name: str) -> NoulAnswer:
    """Return the yes/no answer stored for ``name``."""
    return _typed_answer(response, name, NoulAnswer)


def choice_answer(response: SystemOneResponse, name: str) -> ChoiceAnswer:
    """Return the choice answer stored for ``name``."""
    return _typed_answer(response, name, ChoiceAnswer)


def score_answer(response: SystemOneResponse, name: str) -> ScoreAnswer:
    """Return the score answer stored for ``name``."""
    return _typed_answer(response, name, ScoreAnswer)


def _typed_answer(response: SystemOneResponse, name: str, expected: type[_AnswerT]) -> _AnswerT:
    """Return the answer stored for ``name`` when it has type ``expected``.

    A missing answer, ``None``, or a value of another type raises ``SystemOneError``.
    """
    answers = response.answers
    if not isinstance(answers, dict) or name not in answers:
        raise SystemOneError(f'systemone: answer "{name}" not found')
    value = answers[name]
    if value is None:
        raise SystemOneError(f'systemone: answer "{name}" is nil')
    if not isinstance(value, expected):
        raise SystemOneError(f'systemone: answer "{name}": expected {expected.__name__}, got {type(value).__name__}')
    return value
