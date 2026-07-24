"""Tests for trpc_agent_sdk.tools.safety._exceptions."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._exceptions import (
    SafetyAuditError,
    SafetyGuardError,
    SafetyPolicyError,
    SafetyScannerError,
    ToolRequestError,
)


def test_all_errors_subclass_guard_error():
    for cls in (
            SafetyPolicyError,
            SafetyScannerError,
            SafetyAuditError,
            ToolRequestError,
    ):
        assert issubclass(cls, SafetyGuardError)


def test_guard_error_is_exception():
    assert issubclass(SafetyGuardError, Exception)


def test_errors_raise_and_catch():
    with pytest.raises(SafetyPolicyError):
        raise SafetyPolicyError("bad")
    with pytest.raises(SafetyScannerError):
        raise SafetyScannerError("boom")
    with pytest.raises(SafetyAuditError):
        raise SafetyAuditError("audit")
    with pytest.raises(ToolRequestError):
        raise ToolRequestError("req")


def test_catch_specific_via_base():
    with pytest.raises(SafetyGuardError):
        raise SafetyAuditError("via base")
