# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Host-side redaction. Imports the skill's secret_patterns module so the
sandbox checker and host redaction always share one pattern list."""
import importlib.util
from pathlib import Path

_PATTERNS_PATH = (Path(__file__).resolve().parents[1]
                  / "skills" / "code-review" / "scripts" / "secret_patterns.py")

_spec = importlib.util.spec_from_file_location("cr_secret_patterns", _PATTERNS_PATH)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def redact_text(text):
    """Redact all secrets in *text* using the shared skill patterns."""
    if not text:
        return text
    return _mod.redact(text)


def contains_secret(text):
    """Return True when *text* contains at least one secret match."""
    return bool(_mod.find_secrets(text or ""))
