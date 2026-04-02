# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

"""A2A Agent Example (Standard Protocol over HTTP)."""

from .run_server import create_a2a_service
from .run_server import serve
from .test_a2a import create_runner
from .test_a2a import main
from .test_a2a import run_demo
from .test_a2a import run_remote_agent

__all__ = [
    "create_a2a_service",
    "serve",
    "create_runner",
    "main",
    "run_demo",
    "run_remote_agent",
]



