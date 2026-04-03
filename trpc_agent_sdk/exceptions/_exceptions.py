# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Exceptions for TRPC Agent framework."""

from enum import IntEnum


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


class TrpcAgentException(Exception):
    """TrpcAgent exception"""

    def __init__(self, code: ErrorCode):
        super().__init__(code.phrase)
        self.code = code

    def __str__(self) -> str:
        """Return a string representation of the exception."""
        return f'code: {self.code}, msg: {self.code.phrase}, reason: {self.code.description}'


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
