# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Secret-leakage rules (category: secret_leakage).

Detection reuses the canonical pattern table in :mod:`secret_patterns`
(the same table the host-side redactor uses), so detection and redaction
can never drift apart. Evidence text is pre-redacted here — a leaked
secret must never travel further than this module, not even inside the
sandbox→host findings payload.
"""

from __future__ import annotations

from typing import Any
from typing import Dict
from typing import List

from .rulebase import RuleContext
from .rulebase import SEVERITY_CRITICAL
from .rulebase import is_code_line
from .rulebase import iter_added_lines
from .rulebase import make_finding
from .secret_patterns import find_secrets
from .secret_patterns import redact_text

CATEGORY = "secret_leakage"

_TITLE_BY_ID = {
    "aws_access_key_id": "AWS access key ID committed to source",
    "aws_secret_access_key": "AWS secret access key committed to source",
    "github_token": "GitHub token committed to source",
    "slack_token": "Slack token committed to source",
    "openai_api_key": "API key (sk-*) committed to source",
    "google_api_key": "Google API key committed to source",
    "jwt_token": "JWT committed to source",
    "private_key_block": "Private key material committed to source",
    "bearer_token": "Bearer token committed to source",
    "url_credentials": "Credentials embedded in connection URL",
    "generic_assignment": "Hard-coded credential assignment",
}


def check_file(ctx: RuleContext) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    path = ctx.path
    for lineno, content, _hunk in iter_added_lines(ctx.file_entry):
        if not is_code_line(content):
            continue
        spans = find_secrets(content)
        if not spans:
            continue
        redacted_evidence, _count = redact_text(content)
        for span in spans:
            title = _TITLE_BY_ID.get(span["id"], "Secret committed to source")
            findings.append(
                make_finding(f"SCR_{span['id']}", CATEGORY, SEVERITY_CRITICAL, 0.9, path, lineno,
                             title, redacted_evidence,
                             "Remove the credential from source, rotate it immediately, and load "
                             "it from a secret manager or environment variable instead."))
    return findings
