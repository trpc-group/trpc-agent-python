# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Pytest bootstrap: make the example dir importable (``import codereview``)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
