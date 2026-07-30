"""GitHub webhook request authentication helpers."""

from __future__ import annotations

import hashlib
import hmac


def verify_webhook_signature(body: bytes, secret: str, signature_header: str | None) -> None:
    """Validate GitHub's HMAC-SHA256 signature using constant-time comparison."""
    if not secret:
        raise ValueError("GitHub webhook secret is not configured")
    if not signature_header or not signature_header.startswith("sha256="):
        raise ValueError("Missing or invalid X-Hub-Signature-256 header")
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise ValueError("GitHub webhook signature mismatch")
