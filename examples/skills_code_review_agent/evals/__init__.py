# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Eval module for the code review agent — Phase 3: Evaluation set."""

FIXTURE_DIR = "fixtures"
FIXTURE_NAMES = [
    "01_clean",
    "02_security_leak",
    "03_async_resource_leak",
    "04_db_connection_leak",
    "05_test_missing",
    "06_duplicate_finding",
    "07_sandbox_failure",
    "08_secret_masking",
]

__all__ = ["FIXTURE_DIR", "FIXTURE_NAMES"]