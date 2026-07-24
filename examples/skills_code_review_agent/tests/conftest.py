# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Test path setup for the skills_code_review_agent example."""
import sys
from pathlib import Path

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = EXAMPLE_ROOT / "skills" / "code-review" / "scripts"

for p in (str(EXAMPLE_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)
