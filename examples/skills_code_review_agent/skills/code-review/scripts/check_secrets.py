# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Hardcoded-secret checker. Evidence is redacted before it leaves this script."""
import sys

from checklib import emit, finding, iter_added_py_lines, load_files
from secret_patterns import find_secrets, redact


def main():
    files = load_files(sys.argv)
    findings = []
    for path, line_no, text in iter_added_py_lines(files):
        if find_secrets(text):
            findings.append(finding(
                "critical", "secret_leak", path, line_no,
                "Hardcoded secret committed in source",
                evidence=redact(text.strip()),
                recommendation="Remove the secret, rotate it, and load it from "
                               "environment variables or a secret manager.",
                confidence=0.95))
    emit(findings)


if __name__ == "__main__":
    main()
