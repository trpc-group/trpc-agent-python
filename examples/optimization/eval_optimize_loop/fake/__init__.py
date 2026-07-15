# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Deterministic offline components for the eval/optimization loop."""

from .agent import DeterministicFakeAgent
from .candidate_provider import DeterministicFakeCandidateProvider

__all__ = ["DeterministicFakeAgent", "DeterministicFakeCandidateProvider"]
