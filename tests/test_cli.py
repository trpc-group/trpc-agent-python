# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for _cli module."""

import pytest

from trpc_agent_sdk._cli import (
    _derive_command_path_from_module,
    _normalize_command_path,
    register_cli,
    _REGISTERED_MODULES,
    _REGISTRATIONS,
    main,
)


class TestDeriveCommandPath:
    """Test suite for _derive_command_path_from_module."""

    def test_simple_module(self):
        """Test simple module path derivation."""
        result = _derive_command_path_from_module("trpc_agent_sdk.code_executors._cli")
        assert result == ("code-executors",)

    def test_nested_module(self):
        """Test nested module path derivation."""
        result = _derive_command_path_from_module("trpc_agent_sdk.code_executors.container._cli")
        assert result == ("code-executors", "container")

    def test_no_prefix(self):
        """Test module without package prefix."""
        result = _derive_command_path_from_module("my_module._cli")
        assert result == ("my-module",)

    def test_empty_parts_raises(self):
        """Test empty derived path raises ValueError."""
        with pytest.raises(ValueError, match="Cannot derive"):
            _derive_command_path_from_module("trpc_agent_sdk.__init__")


class TestNormalizeCommandPath:
    """Test suite for _normalize_command_path."""

    def test_strips_whitespace(self):
        """Test segments are stripped."""
        result = _normalize_command_path([" code ", " exec "])
        assert result == ("code", "exec")

    def test_empty_raises(self):
        """Test empty path raises ValueError."""
        with pytest.raises(ValueError, match="at least one"):
            _normalize_command_path([])

    def test_blank_segments_filtered(self):
        """Test blank segments are filtered."""
        with pytest.raises(ValueError):
            _normalize_command_path(["", "  "])


class TestRegisterCli:
    """Test suite for register_cli."""

    def test_registers_module(self):
        """Test register_cli adds a registration."""
        test_module = "test_module_for_cli_test_12345"
        initial_count = len(_REGISTRATIONS)
        register_cli(test_module, command_path=["test"])
        assert test_module in _REGISTERED_MODULES
        assert len(_REGISTRATIONS) > initial_count
        _REGISTERED_MODULES.discard(test_module)
        _REGISTRATIONS.pop()

    def test_duplicate_ignored(self):
        """Test duplicate registration is ignored."""
        test_module = "test_module_dup_cli_12345"
        register_cli(test_module, command_path=["test"])
        count = len(_REGISTRATIONS)
        register_cli(test_module, command_path=["test2"])
        assert len(_REGISTRATIONS) == count
        _REGISTERED_MODULES.discard(test_module)
        _REGISTRATIONS.pop()


class TestMain:
    """Test suite for main()."""

    def test_help_returns_zero(self):
        """Test --help returns 0."""
        result = main(["--help"])
        assert result == 0

    def test_no_args_shows_help(self):
        """Test no args shows help (exits with 0 or 2)."""
        result = main([])
        assert result in (0, 2)
