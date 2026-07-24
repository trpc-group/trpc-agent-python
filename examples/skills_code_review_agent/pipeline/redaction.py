"""Sensitive information redaction — sanitize outputs before storage.

Ensures no API keys, tokens, or passwords appear in reports or database.
"""

import re

# Redaction patterns: (regex, replacement, label)
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # OpenAI keys (match before generic API key pattern)
    (re.compile(r'sk-[A-Za-z0-9]{20,}'), "sk-***", "OpenAI API key"),
    # GitHub tokens
    (re.compile(r'(?:ghp|github_pat|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}'),
     "ghp_***", "GitHub token"),
    # JWT tokens (match before shorter patterns)
    (re.compile(r'eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+'),
     "JWT***", "JWT token"),
    # AWS keys
    (re.compile(r'AKIA[0-9A-Z]{16}'), "AKIA***", "AWS access key"),
    # Connection strings with credentials
    (re.compile(r'(?:mongodb|mysql|postgresql|redis)://[^@\s]+@',
                re.IGNORECASE),
     "db://***:***@", "Database connection string"),
    # Private key headers
    (re.compile(r'-----BEGIN (?:RSA|EC|DSA|OPENSSH) PRIVATE KEY-----'),
     "-----BEGIN *** PRIVATE KEY-----", "Private key"),
    # Generic API key assignments
    (re.compile(r'(?:api[_-]?key|apikey|API_KEY)\s*[=:]\s*["\']?\S{8,}["\']?',
                re.IGNORECASE),
     "API_KEY=***", "API key assignment"),
    # Passwords
    (re.compile(r'(?:password|passwd|pwd)\s*[=:]\s*["\']\S+["\']', re.IGNORECASE),
     "password=***", "Password assignment"),
    # Tokens/secrets
    (re.compile(r'(?:secret|token|AUTH_TOKEN)\s*[=:]\s*["\'][A-Za-z0-9_\-]{8,}["\']',
                re.IGNORECASE),
     "token=***", "Secret/token assignment"),
    # AWS credential keys (names)
    (re.compile(r'(?:aws_access_key_id|aws_secret_access_key)\s*[=:]\s*\S+',
                re.IGNORECASE),
     "aws_key=***", "AWS credential"),
]


def redact(text: str) -> tuple[str, int]:
    """Redact sensitive information from text.

    Args:
        text: Input text to scan for sensitive data.

    Returns:
        (redacted_text, count_of_redactions)
    """
    count = 0
    result = text
    for pattern, replacement, _label in _PATTERNS:
        new_result, n = pattern.subn(replacement, result)
        if n > 0:
            count += n
            result = new_result
    return result, count


def redact_finding_evidence(findings: list) -> tuple[list, int]:
    """Redact evidence fields in a list of findings.

    Returns (redacted_findings_list, total_redactions).
    """
    total = 0
    for f in findings:
        if hasattr(f, 'evidence') and f.evidence:
            f.evidence, n = redact(f.evidence)
            total += n
    return findings, total


def should_redact(text: str) -> bool:
    """Quick check if text contains potentially sensitive information."""
    for pattern, _, _ in _PATTERNS:
        if pattern.search(text):
            return True
    return False
