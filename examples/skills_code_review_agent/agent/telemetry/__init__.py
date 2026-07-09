# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Telemetry package — OTel tracing + monitor_summary recorder."""
from .tracing import TelemetryRecorder
from .tracing import get_tracer
from .tracing import init_telemetry
from .tracing import trace_stage

__all__ = ["init_telemetry", "get_tracer", "trace_stage", "TelemetryRecorder"]
