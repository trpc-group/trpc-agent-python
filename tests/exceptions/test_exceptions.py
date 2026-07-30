# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.exceptions._exceptions."""

from __future__ import annotations

from enum import IntEnum

import pytest

from trpc_agent_sdk.exceptions._exceptions import (
    AgentFilterError,
    ArtifactServiceNotFound,
    ErrorCode,
    LLMAgentModelNotFound,
    ParentAgentNotFound,
    RunCancelledException,
    TrpcAgentException,
)


# ---------------------------------------------------------------------------
# ErrorCode
# ---------------------------------------------------------------------------


class TestErrorCode:
    """Tests for the ErrorCode IntEnum."""

    def test_is_int_enum(self):
        assert issubclass(ErrorCode, IntEnum)

    @pytest.mark.parametrize(
        "member, expected_value",
        [
            (ErrorCode.OK, 0),
            (ErrorCode.PARENT_AGENT_NOT_FOUND, 601),
            (ErrorCode.AGENT_FILTER_ERROR, 602),
            (ErrorCode.ARTIFACT_SERVICE_NOT_FOUND, 603),
            (ErrorCode.LLM_AGENT_MODEL_NOT_FOUND, 604),
            (ErrorCode.RUN_CANCELLED, 605),
        ],
    )
    def test_member_values(self, member: ErrorCode, expected_value: int):
        assert member == expected_value
        assert int(member) == expected_value

    @pytest.mark.parametrize(
        "member, expected_phrase",
        [
            (ErrorCode.OK, "OK"),
            (ErrorCode.PARENT_AGENT_NOT_FOUND, "parent agent not found"),
            (ErrorCode.AGENT_FILTER_ERROR, "agent filter error"),
            (ErrorCode.ARTIFACT_SERVICE_NOT_FOUND, "artifact_service not found"),
            (ErrorCode.LLM_AGENT_MODEL_NOT_FOUND, "model not found"),
            (ErrorCode.RUN_CANCELLED, "run cancelled"),
        ],
    )
    def test_member_phrases(self, member: ErrorCode, expected_phrase: str):
        assert member.phrase == expected_phrase

    @pytest.mark.parametrize(
        "member, expected_description",
        [
            (ErrorCode.OK, "Request fulfilled, document follows"),
            (ErrorCode.PARENT_AGENT_NOT_FOUND, "the parent agent of current agent not found"),
            (ErrorCode.AGENT_FILTER_ERROR, "the filter of agent is error name"),
            (ErrorCode.ARTIFACT_SERVICE_NOT_FOUND, "the artifact_service maybe is none"),
            (ErrorCode.LLM_AGENT_MODEL_NOT_FOUND, "the artifact not found"),
            (ErrorCode.RUN_CANCELLED, "the run was cancelled by user request"),
        ],
    )
    def test_member_descriptions(self, member: ErrorCode, expected_description: str):
        assert member.description == expected_description

    def test_total_member_count(self):
        assert len(ErrorCode) == 6

    def test_can_be_used_as_int(self):
        assert ErrorCode.OK + 1 == 1
        assert ErrorCode.PARENT_AGENT_NOT_FOUND > 600

    def test_lookup_by_value(self):
        assert ErrorCode(0) is ErrorCode.OK
        assert ErrorCode(601) is ErrorCode.PARENT_AGENT_NOT_FOUND
        assert ErrorCode(605) is ErrorCode.RUN_CANCELLED

    def test_lookup_by_name(self):
        assert ErrorCode["OK"] is ErrorCode.OK
        assert ErrorCode["RUN_CANCELLED"] is ErrorCode.RUN_CANCELLED

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ErrorCode(999)

    def test_invalid_name_raises(self):
        with pytest.raises(KeyError):
            ErrorCode["NONEXISTENT"]


# ---------------------------------------------------------------------------
# TrpcAgentException
# ---------------------------------------------------------------------------


class TestTrpcAgentException:
    """Tests for the TrpcAgentException base class."""

    def test_is_exception_subclass(self):
        assert issubclass(TrpcAgentException, Exception)

    def test_init_stores_code(self):
        exc = TrpcAgentException(ErrorCode.OK)
        assert exc.code is ErrorCode.OK

    def test_init_sets_args_to_phrase(self):
        exc = TrpcAgentException(ErrorCode.OK)
        assert exc.args == ("OK",)

    def test_str_format(self):
        exc = TrpcAgentException(ErrorCode.PARENT_AGENT_NOT_FOUND)
        result = str(exc)
        assert "code: 601" in result
        assert "msg: parent agent not found" in result
        assert "reason: the parent agent of current agent not found" in result

    def test_str_for_all_codes(self):
        for code in ErrorCode:
            exc = TrpcAgentException(code)
            s = str(exc)
            assert f"code: {int(code)}" in s
            assert f"msg: {code.phrase}" in s
            assert f"reason: {code.description}" in s

    def test_can_be_raised_and_caught(self):
        with pytest.raises(TrpcAgentException) as exc_info:
            raise TrpcAgentException(ErrorCode.AGENT_FILTER_ERROR)
        assert exc_info.value.code is ErrorCode.AGENT_FILTER_ERROR

    def test_can_be_caught_as_exception(self):
        with pytest.raises(Exception):
            raise TrpcAgentException(ErrorCode.OK)

    def test_repr_contains_phrase(self):
        exc = TrpcAgentException(ErrorCode.OK)
        assert "OK" in repr(exc)


# ---------------------------------------------------------------------------
# RunCancelledException
# ---------------------------------------------------------------------------


class TestRunCancelledException:
    """Tests for RunCancelledException."""

    def test_is_trpc_agent_exception_subclass(self):
        assert issubclass(RunCancelledException, TrpcAgentException)

    def test_is_exception_subclass(self):
        assert issubclass(RunCancelledException, Exception)

    def test_default_message(self):
        exc = RunCancelledException()
        assert exc.message == "Run cancelled by user"

    def test_custom_message(self):
        exc = RunCancelledException(message="custom cancel reason")
        assert exc.message == "custom cancel reason"

    def test_code_is_run_cancelled(self):
        exc = RunCancelledException()
        assert exc.code is ErrorCode.RUN_CANCELLED

    def test_code_with_custom_message(self):
        exc = RunCancelledException("something else")
        assert exc.code is ErrorCode.RUN_CANCELLED

    def test_str_returns_message(self):
        exc = RunCancelledException()
        assert str(exc) == "Run cancelled by user"

    def test_str_returns_custom_message(self):
        exc = RunCancelledException(message="stopped")
        assert str(exc) == "stopped"

    def test_str_does_not_use_parent_format(self):
        exc = RunCancelledException()
        assert "code:" not in str(exc)

    def test_can_be_raised_and_caught_as_trpc_exception(self):
        with pytest.raises(TrpcAgentException):
            raise RunCancelledException()

    def test_can_be_raised_and_caught_specifically(self):
        with pytest.raises(RunCancelledException) as exc_info:
            raise RunCancelledException("abort")
        assert exc_info.value.message == "abort"
        assert exc_info.value.code is ErrorCode.RUN_CANCELLED


# ---------------------------------------------------------------------------
# Pre-built exception instances
# ---------------------------------------------------------------------------


class TestPreBuiltExceptions:
    """Tests for the module-level singleton exception instances."""

    def test_parent_agent_not_found_code(self):
        assert ParentAgentNotFound.code is ErrorCode.PARENT_AGENT_NOT_FOUND

    def test_agent_filter_error_code(self):
        assert AgentFilterError.code is ErrorCode.AGENT_FILTER_ERROR

    def test_artifact_service_not_found_code(self):
        assert ArtifactServiceNotFound.code is ErrorCode.ARTIFACT_SERVICE_NOT_FOUND

    def test_llm_agent_model_not_found_code(self):
        assert LLMAgentModelNotFound.code is ErrorCode.LLM_AGENT_MODEL_NOT_FOUND

    def test_all_are_trpc_agent_exception_instances(self):
        for instance in (
            ParentAgentNotFound,
            AgentFilterError,
            ArtifactServiceNotFound,
            LLMAgentModelNotFound,
        ):
            assert isinstance(instance, TrpcAgentException)

    def test_all_are_exception_instances(self):
        for instance in (
            ParentAgentNotFound,
            AgentFilterError,
            ArtifactServiceNotFound,
            LLMAgentModelNotFound,
        ):
            assert isinstance(instance, Exception)

    def test_str_representations(self):
        assert "parent agent not found" in str(ParentAgentNotFound)
        assert "agent filter error" in str(AgentFilterError)
        assert "artifact_service not found" in str(ArtifactServiceNotFound)
        assert "model not found" in str(LLMAgentModelNotFound)

    @pytest.mark.parametrize(
        "instance, code",
        [
            (ParentAgentNotFound, ErrorCode.PARENT_AGENT_NOT_FOUND),
            (AgentFilterError, ErrorCode.AGENT_FILTER_ERROR),
            (ArtifactServiceNotFound, ErrorCode.ARTIFACT_SERVICE_NOT_FOUND),
            (LLMAgentModelNotFound, ErrorCode.LLM_AGENT_MODEL_NOT_FOUND),
        ],
    )
    def test_can_be_raised_and_caught(self, instance, code):
        with pytest.raises(TrpcAgentException) as exc_info:
            raise instance
        assert exc_info.value.code is code

    def test_instances_are_not_run_cancelled(self):
        for instance in (
            ParentAgentNotFound,
            AgentFilterError,
            ArtifactServiceNotFound,
            LLMAgentModelNotFound,
        ):
            assert not isinstance(instance, RunCancelledException)


# ---------------------------------------------------------------------------
# Public API (__init__.py exports)
# ---------------------------------------------------------------------------


class TestPublicAPI:
    """Tests that the public surface of trpc_agent_sdk.exceptions is correct."""

    def test_all_names_importable(self):
        import trpc_agent_sdk.exceptions as mod

        expected = [
            "AgentFilterError",
            "ArtifactServiceNotFound",
            "ErrorCode",
            "LLMAgentModelNotFound",
            "ParentAgentNotFound",
            "RunCancelledException",
            "TrpcAgentException",
        ]
        for name in expected:
            assert hasattr(mod, name), f"{name} not found in trpc_agent_sdk.exceptions"

    def test_all_attribute(self):
        import trpc_agent_sdk.exceptions as mod

        expected_all = {
            "AgentFilterError",
            "ArtifactServiceNotFound",
            "ErrorCode",
            "LLMAgentModelNotFound",
            "ParentAgentNotFound",
            "RunCancelledException",
            "TrpcAgentException",
        }
        assert set(mod.__all__) == expected_all

    def test_all_matches_public_exports(self):
        import trpc_agent_sdk.exceptions as mod

        for name in mod.__all__:
            assert hasattr(mod, name), f"__all__ lists {name} but it is not an attribute"
