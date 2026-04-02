# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.common._compatible.

Covers:
- PY_310 version flag
- checkenum() with standard enums, IntEnum, and fallback path
- OSDetector: platform detection, properties, get_os_name, get_os_info, __str__
- OS_DETECTOR module-level singleton
"""

from __future__ import annotations

import enum
import platform
import sys
from unittest.mock import patch

import pytest

from trpc_agent_sdk.common._compatible import OS_DETECTOR, OSDetector, PY_310, checkenum


# ---------------------------------------------------------------------------
# PY_310
# ---------------------------------------------------------------------------


class TestPY310:
    """Tests for the PY_310 version flag."""

    def test_py310_is_bool(self):
        assert isinstance(PY_310, bool)

    def test_py310_matches_runtime(self):
        assert PY_310 == (sys.version_info >= (3, 10))


# ---------------------------------------------------------------------------
# checkenum
# ---------------------------------------------------------------------------


class _Color(enum.Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class _Priority(enum.IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


class _FlagEnum(enum.Flag):
    READ = 1
    WRITE = 2
    EXECUTE = 4


class TestCheckenum:
    """Tests for checkenum()."""

    def test_valid_enum_member(self):
        assert checkenum(_Color.RED, _Color) is True

    def test_invalid_enum_member(self):
        assert checkenum("yellow", _Color) is False

    def test_valid_int_enum_member(self):
        assert checkenum(_Priority.HIGH, _Priority) is True

    def test_invalid_int_enum_member(self):
        assert checkenum(99, _Priority) is False

    def test_valid_flag_enum_member(self):
        assert checkenum(_FlagEnum.READ, _FlagEnum) is True

    def test_string_value_is_found_by_value(self):
        # Python 3.12+ enum __contains__ matches by value
        assert checkenum("red", _Color) is True

    def test_string_not_matching_any_value(self):
        assert checkenum("magenta", _Color) is False

    def test_none_is_not_member(self):
        assert checkenum(None, _Color) is False

    def test_fallback_to_members_values(self):
        """When ``in`` raises, falls back to __members__.values()."""

        class _BadContains:
            """Fake enum-like class whose ``__contains__`` always raises."""

            class _Members:
                def values(self):
                    return ["a", "b"]

            __members__ = _Members()

            def __contains__(self, item):
                raise TypeError("broken __contains__")

            def __iter__(self):
                raise TypeError("broken __iter__")

        assert checkenum("a", _BadContains()) is True
        assert checkenum("c", _BadContains()) is False


# ---------------------------------------------------------------------------
# OSDetector
# ---------------------------------------------------------------------------


class TestOSDetectorInit:
    """Tests for OSDetector initialisation and _detect_os."""

    def test_os_info_keys(self):
        detector = OSDetector()
        info = detector.get_os_info()
        expected_keys = {
            "system",
            "platform",
            "release",
            "version",
            "machine",
            "processor",
            "architecture",
            "node",
            "python_version",
            "python_implementation",
        }
        assert set(info.keys()) == expected_keys

    def test_os_info_values_are_not_none(self):
        detector = OSDetector()
        info = detector.get_os_info()
        for key in ("system", "platform", "python_version", "python_implementation"):
            assert info[key] is not None

    def test_get_os_info_returns_copy(self):
        detector = OSDetector()
        info1 = detector.get_os_info()
        info2 = detector.get_os_info()
        assert info1 == info2
        assert info1 is not info2


class TestOSDetectorWindows:
    """Tests for Windows detection via mocked platform info."""

    @pytest.fixture
    def windows_detector(self):
        with patch.object(platform, "system", return_value="Windows"), \
             patch.object(sys, "platform", "win32"), \
             patch.object(platform, "release", return_value="10"), \
             patch.object(platform, "version", return_value="10.0.19041"), \
             patch.object(platform, "machine", return_value="AMD64"), \
             patch.object(platform, "processor", return_value="Intel64"), \
             patch.object(platform, "architecture", return_value=("64bit", "WindowsPE")), \
             patch.object(platform, "node", return_value="WIN-PC"):
            yield OSDetector()

    def test_is_windows(self, windows_detector):
        assert windows_detector.is_windows is True

    def test_is_not_macos(self, windows_detector):
        assert windows_detector.is_macos is False

    def test_is_not_linux(self, windows_detector):
        assert windows_detector.is_linux is False

    def test_is_not_unix(self, windows_detector):
        assert windows_detector.is_unix is False

    def test_get_os_name(self, windows_detector):
        assert windows_detector.get_os_name() == "Windows"

    def test_str(self, windows_detector):
        assert str(windows_detector) == "OSDetector(Windows)"


class TestOSDetectorMacOS:
    """Tests for macOS detection via mocked platform info."""

    @pytest.fixture
    def macos_detector(self):
        with patch.object(platform, "system", return_value="Darwin"), \
             patch.object(sys, "platform", "darwin"), \
             patch.object(platform, "release", return_value="23.0.0"), \
             patch.object(platform, "version", return_value="Darwin Kernel 23.0.0"), \
             patch.object(platform, "machine", return_value="arm64"), \
             patch.object(platform, "processor", return_value="arm"), \
             patch.object(platform, "architecture", return_value=("64bit", "")), \
             patch.object(platform, "node", return_value="MacBook.local"):
            yield OSDetector()

    def test_is_macos(self, macos_detector):
        assert macos_detector.is_macos is True

    def test_is_not_windows(self, macos_detector):
        assert macos_detector.is_windows is False

    def test_is_not_linux(self, macos_detector):
        assert macos_detector.is_linux is False

    def test_is_unix(self, macos_detector):
        assert macos_detector.is_unix is True

    def test_get_os_name(self, macos_detector):
        assert macos_detector.get_os_name() == "macOS"

    def test_str(self, macos_detector):
        assert str(macos_detector) == "OSDetector(macOS)"


class TestOSDetectorLinux:
    """Tests for Linux detection via mocked platform info."""

    @pytest.fixture
    def linux_detector(self):
        with patch.object(platform, "system", return_value="Linux"), \
             patch.object(sys, "platform", "linux"), \
             patch.object(platform, "release", return_value="5.15.0"), \
             patch.object(platform, "version", return_value="#1 SMP"), \
             patch.object(platform, "machine", return_value="x86_64"), \
             patch.object(platform, "processor", return_value="x86_64"), \
             patch.object(platform, "architecture", return_value=("64bit", "ELF")), \
             patch.object(platform, "node", return_value="linux-host"):
            yield OSDetector()

    def test_is_linux(self, linux_detector):
        assert linux_detector.is_linux is True

    def test_is_not_windows(self, linux_detector):
        assert linux_detector.is_windows is False

    def test_is_not_macos(self, linux_detector):
        assert linux_detector.is_macos is False

    def test_is_unix(self, linux_detector):
        assert linux_detector.is_unix is True

    def test_get_os_name(self, linux_detector):
        assert linux_detector.get_os_name() == "Linux"

    def test_str(self, linux_detector):
        assert str(linux_detector) == "OSDetector(Linux)"


class TestOSDetectorUnknown:
    """Tests for unknown OS when none of the known systems match."""

    @pytest.fixture
    def unknown_detector(self):
        with patch.object(platform, "system", return_value="FreeBSD"), \
             patch.object(sys, "platform", "freebsd13"), \
             patch.object(platform, "release", return_value="13.0"), \
             patch.object(platform, "version", return_value="FreeBSD 13.0"), \
             patch.object(platform, "machine", return_value="amd64"), \
             patch.object(platform, "processor", return_value="amd64"), \
             patch.object(platform, "architecture", return_value=("64bit", "ELF")), \
             patch.object(platform, "node", return_value="bsd-host"):
            yield OSDetector()

    def test_is_not_windows(self, unknown_detector):
        assert unknown_detector.is_windows is False

    def test_is_not_macos(self, unknown_detector):
        assert unknown_detector.is_macos is False

    def test_is_not_linux(self, unknown_detector):
        assert unknown_detector.is_linux is False

    def test_is_not_unix(self, unknown_detector):
        assert unknown_detector.is_unix is False

    def test_get_os_name_returns_unknown(self, unknown_detector):
        assert unknown_detector.get_os_name() == "Unknown"

    def test_str(self, unknown_detector):
        assert str(unknown_detector) == "OSDetector(Unknown)"


class TestOSDetectorPlatformFallback:
    """Tests that the ``platform`` field fallback works for detection.

    The ``is_*`` properties check both ``system`` and ``platform`` (sys.platform).
    This verifies the second condition in each ``or`` clause.
    """

    def test_windows_via_platform_only(self):
        with patch.object(platform, "system", return_value="Other"), \
             patch.object(sys, "platform", "win32"), \
             patch.object(platform, "release", return_value=""), \
             patch.object(platform, "version", return_value=""), \
             patch.object(platform, "machine", return_value=""), \
             patch.object(platform, "processor", return_value=""), \
             patch.object(platform, "architecture", return_value=("", "")), \
             patch.object(platform, "node", return_value=""):
            d = OSDetector()
            assert d.is_windows is True

    def test_macos_via_platform_only(self):
        with patch.object(platform, "system", return_value="Other"), \
             patch.object(sys, "platform", "darwin"), \
             patch.object(platform, "release", return_value=""), \
             patch.object(platform, "version", return_value=""), \
             patch.object(platform, "machine", return_value=""), \
             patch.object(platform, "processor", return_value=""), \
             patch.object(platform, "architecture", return_value=("", "")), \
             patch.object(platform, "node", return_value=""):
            d = OSDetector()
            assert d.is_macos is True

    def test_linux_via_platform_only(self):
        with patch.object(platform, "system", return_value="Other"), \
             patch.object(sys, "platform", "linux"), \
             patch.object(platform, "release", return_value=""), \
             patch.object(platform, "version", return_value=""), \
             patch.object(platform, "machine", return_value=""), \
             patch.object(platform, "processor", return_value=""), \
             patch.object(platform, "architecture", return_value=("", "")), \
             patch.object(platform, "node", return_value=""):
            d = OSDetector()
            assert d.is_linux is True


# ---------------------------------------------------------------------------
# OS_DETECTOR singleton
# ---------------------------------------------------------------------------


class TestOSDetectorSingleton:
    """Tests for the module-level OS_DETECTOR instance."""

    def test_is_instance(self):
        assert isinstance(OS_DETECTOR, OSDetector)

    def test_os_info_matches_platform(self):
        info = OS_DETECTOR.get_os_info()
        assert info["python_version"] == platform.python_version()
        assert info["python_implementation"] == platform.python_implementation()

    def test_at_least_one_os_detected(self):
        assert any([
            OS_DETECTOR.is_windows,
            OS_DETECTOR.is_macos,
            OS_DETECTOR.is_linux,
        ])
