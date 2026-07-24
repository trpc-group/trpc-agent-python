# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Reports module for the code review agent — Phase 2: Report generation."""

from .generator import (
    generate_json_report,
    generate_markdown_report,
    write_reports,
)

__all__ = ["generate_json_report", "generate_markdown_report", "write_reports"]