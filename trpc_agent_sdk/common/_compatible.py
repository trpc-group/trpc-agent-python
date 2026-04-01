# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Compatible Python Version Check Module"""

import platform
import sys
from typing import Any
from typing import Dict

PY_310 = sys.version_info >= (3, 10)


def checkenum(value, enum_class) -> bool:
    """Check if a value is a valid member of an enum class."""
    try:
        return value in enum_class
    except Exception:  # pylint: disable=broad-except
        return value in enum_class.__members__.values()


class OSDetector:
    """OS Detector"""

    def __init__(self):
        self._os_info = self._detect_os()

    def _detect_os(self) -> Dict[str, Any]:
        """Detect OS information"""
        return {
            'system': platform.system(),
            'platform': sys.platform,
            'release': platform.release(),
            'version': platform.version(),
            'machine': platform.machine(),
            'processor': platform.processor(),
            'architecture': platform.architecture(),
            'node': platform.node(),
            'python_version': platform.python_version(),
            'python_implementation': platform.python_implementation()
        }

    @property
    def is_windows(self) -> bool:
        """Is Windows"""
        return self._os_info['system'] == 'Windows' or self._os_info['platform'].startswith('win')

    @property
    def is_macos(self) -> bool:
        """Is macOS"""
        return self._os_info['system'] == 'Darwin' or self._os_info['platform'].startswith('darwin')

    @property
    def is_linux(self) -> bool:
        """Is Linux"""
        return self._os_info['system'] == 'Linux' or self._os_info['platform'].startswith('linux')

    @property
    def is_unix(self) -> bool:
        """Is Unix system (including macOS and Linux)"""
        return self.is_macos or self.is_linux

    def get_os_name(self) -> str:
        """Get OS name"""
        if self.is_windows:
            return 'Windows'
        elif self.is_macos:
            return 'macOS'
        elif self.is_linux:
            return 'Linux'
        else:
            return 'Unknown'

    def get_os_info(self) -> Dict[str, Any]:
        """Get complete OS information"""
        return self._os_info.copy()

    def __str__(self) -> str:
        return f"OSDetector({self.get_os_name()})"


OS_DETECTOR = OSDetector()
