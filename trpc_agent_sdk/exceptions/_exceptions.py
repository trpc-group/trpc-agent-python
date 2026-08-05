# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Exceptions for TRPC Agent framework."""

from enum import Enum
from enum import IntEnum
from typing import Union


class RunLimitType(str, Enum):
    """Types of invocation-local count limits."""

    MAX_LLM_CALLS = "max_llm_calls"
    MAX_ITERATIONS = "max_iterations"
    MAX_TOOL_CALLS = "max_tool_calls"


class ErrorCode(IntEnum):

    def __new__(cls, value, phrase, description=''):
        obj = int.__new__(cls, value)
        obj._value_ = value

        obj.phrase = phrase
        obj.description = description
        return obj

    # informational
    OK = 0, 'OK', 'Request fulfilled, document follows'
    PARENT_AGENT_NOT_FOUND = (601, 'parent agent not found', 'the parent agent of current agent not found')
    AGENT_FILTER_ERROR = 602, 'agent filter error', 'the filter of agent is error name'
    ARTIFACT_SERVICE_NOT_FOUND = 603, 'artifact_service not found', 'the artifact_service maybe is none'
    LLM_AGENT_MODEL_NOT_FOUND = 604, 'model not found', 'the artifact not found'
    RUN_CANCELLED = 605, 'run cancelled', 'the run was cancelled by user request'
    RUN_LIMIT_EXCEEDED = 606, 'run limit exceeded', 'the agent invocation reached a configured run limit'


class TrpcAgentException(Exception):
    """TrpcAgent exception"""

    def __init__(self, code: ErrorCode):
        super().__init__(code.phrase)
        self.code = code

    def __str__(self) -> str:
        """Return a string representation of the exception."""
        return f'code: {self.code}, msg: {self.code.phrase}, reason: {self.code.description}'


class RunLimitException(TrpcAgentException):
    """Exception raised when an agent invocation exceeds a configured limit.

    Attributes:
        agent_name: Name of the agent whose invocation reached the limit.
        limit_type: Type of the configured limit.
        configured_value: Maximum value configured for the limit.
        observed_value: Value observed when the limit was detected.
    """

    def __init__(
        self,
        *,
        agent_name: str,
        limit_type: RunLimitType,
        configured_value: int,
        observed_value: int,
    ) -> None:
        super().__init__(ErrorCode.RUN_LIMIT_EXCEEDED)
        self.agent_name = agent_name
        self.limit_type = limit_type
        self.configured_value = configured_value
        self.observed_value = observed_value
        self.message = f"Agent '{agent_name}' reached {limit_type.value}={configured_value}."

    def __str__(self) -> str:
        """Return the limit-specific error message."""
        return self.message

    @property
    def error_code(self) -> str:
        """Return the stable error code for protocol and telemetry adapters."""
        return f"{self.limit_type.value}_exceeded"

    def get_custom_metadata(self) -> dict[str, Union[str, int]]:
        """Return JSON-serializable details for protocol adapters."""
        return {
            "limit_type": self.limit_type.value,
            "configured_value": self.configured_value,
            "observed_value": self.observed_value,
            "agent_name": self.agent_name,
        }


class RunCancelledException(TrpcAgentException):
    """Exception raised when a run is cancelled.

    This exception is raised at cancellation checkpoints when the
    cancellation manager detects that a run has been cancelled.
    """

    def __init__(self, message: str = "Run cancelled by user"):
        super().__init__(ErrorCode.RUN_CANCELLED)
        self.message = message

    def __str__(self) -> str:
        return self.message


ParentAgentNotFound = TrpcAgentException(ErrorCode.PARENT_AGENT_NOT_FOUND)
AgentFilterError = TrpcAgentException(ErrorCode.AGENT_FILTER_ERROR)
ArtifactServiceNotFound = TrpcAgentException(ErrorCode.ARTIFACT_SERVICE_NOT_FOUND)
LLMAgentModelNotFound = TrpcAgentException(ErrorCode.LLM_AGENT_MODEL_NOT_FOUND)
