# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""Built-in safety rules registry."""
from __future__ import annotations

from .base import SafetyRule
from .dangerous_files import DangerousFilesRule
from .dependency_install import DependencyInstallRule
from .network import NetworkRule
from .process import ProcessRule
from .resource_abuse import ResourceAbuseRule
from .secret_leak import SecretLeakRule


def default_rules() -> list[SafetyRule]:
    """Return the default ordered set of built-in safety rules."""
    return [
        DangerousFilesRule(),
        NetworkRule(),
        ProcessRule(),
        DependencyInstallRule(),
        ResourceAbuseRule(),
        SecretLeakRule(),
    ]


__all__ = [
    "SafetyRule",
    "DangerousFilesRule",
    "NetworkRule",
    "ProcessRule",
    "DependencyInstallRule",
    "ResourceAbuseRule",
    "SecretLeakRule",
    "default_rules",
]
