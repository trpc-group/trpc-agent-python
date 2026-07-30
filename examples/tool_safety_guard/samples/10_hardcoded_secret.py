#!/usr/bin/env python3
"""Sample 10 — SENSITIVE INFO: a hardcoded credential literal.

Expected verdict: deny (SEC001/SEC003, high). The secret value is masked in the
report and audit log because ``redact_sensitive`` is enabled.
"""

OPENAI_API_KEY = "sk-abcdef1234567890ABCDEFghijklmnop"


def client_headers() -> dict:
    return {"Authorization": f"Bearer {OPENAI_API_KEY}"}
