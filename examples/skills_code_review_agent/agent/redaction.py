"""Secret detection and redaction helpers.

Three layers, applied in order:

1. Keyword assignments (``aws_secret_access_key = "..."``, ``"password": "..."``).
   The keyword is matched *inside* an identifier rather than as a standalone
   word, because real credentials are almost always named
   ``DATABASE_PASSWORD`` or ``SENDGRID_API_KEY``, never a bare ``password``.
2. Provider-shaped tokens (AWS, GitHub, Slack, Stripe, Google, SendGrid, npm,
   Twilio, Alibaba, JWT, PEM blocks, credentials embedded in URLs).
3. An entropy fallback for long opaque values on lines that already look
   credential-related, which catches providers nobody wrote a pattern for.

Layer 1 deliberately skips values that are plainly code references
(``user.password_hash``, ``os.environ["APP_PASSWORD"]``) so that widening
recall does not silently corrupt reports. ``evalset/secrets_corpus.json``
measures both directions: recall over real credentials and the false-redaction
rate over benign lines.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import is_dataclass, replace
from typing import Any

REDACTION_TOKEN = "<REDACTED>"

# Keyword fragments that mark an identifier as credential-bearing. Deliberately
# excludes bare "key" and "id", which appear in far too many benign names.
_KEYWORD = (
    r"api[_-]?key|access[_-]?key|secret[_-]?key|storage[_-]?key|signing[_-]?key|private[_-]?key|"
    r"access[_-]?token|auth[_-]?token|refresh[_-]?token|id[_-]?token|bearer[_-]?token|"
    r"client[_-]?secret|credential|passphrase|password|passwd|pwd|secret|token"
)

# An identifier that *contains* one of the keywords, optionally quoted as a JSON
# or YAML key, followed by an assignment and a value of at least six characters.
KEY_VALUE_RE = re.compile(
    r"(?i)(?P<key>[A-Za-z0-9_.\-]*(?:" + _KEYWORD + r")[A-Za-z0-9_.\-]*)"
    r"(?P<sep>[\"']?\s*[:=]\s*)"
    r"(?P<quote>[\"']?)"
    # Brackets and braces are excluded from the value: no real credential
    # contains them, and allowing them makes `{"password": password}` capture
    # the trailing brace, which defeats the identifier check below.
    r"(?P<value>[^\"'()\[\]{}\s,;#]{6,})"
    r"(?P=quote)"
    r"(?=$|[\s,;#\]}])")

# Values that are code, not credentials: dotted lowercase attribute access and
# environment lookups. Uppercase or mixed-case segments are not excluded, so
# provider tokens such as SendGrid's "SG.aB1c.pQ8r" still redact.
CODE_REFERENCE_RE = re.compile(r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)+$")
ENV_LOOKUP_RE = re.compile(r"^os\.(environ|getenv)\b")

# A bare identifier passed as a keyword argument or dict value is a reference to
# a credential, not the credential itself: `boto3.client(aws_secret_access_key=secret_key)`.
# Requiring an unquoted value inside a call or literal keeps .env style
# assignments such as `DATABASE_PASSWORD=hunter2hunter2` in scope.
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Documented placeholders are not credentials. Reporting them as leaked secrets
# is the single most common false positive in configuration samples.
#
# The marker has to be the whole value, a bracketed template, or a SCREAMING_CASE
# token made only of letters and underscores. Matching it as a bare substring is
# what breaks here: AWS publishes "AKIAIOSFODNN7EXAMPLE" as its documented key
# shape, and real leaked keys routinely contain those letters by chance.
PLACEHOLDER_RE = re.compile(
    r"(?i)^(?:<[^>]*>|\{\{?[^}]*\}?\}|x{4,}|\*{4,}|\.{3,}"
    r"|changeme|placeholder|example|sample|dummy|todo|tbd|fake|notreal)$"
    r"|^[A-Z_]*(?:REPLACE|CHANGE|YOUR|PLACEHOLDER|EXAMPLE|SAMPLE|DUMMY|TODO|TBD|HERE|ME)[A-Z_]*$")

PROVIDER_PATTERNS: list[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]]] = [
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{10,}"), "Bearer " + REDACTION_TOKEN),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), REDACTION_TOKEN),
    (re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{16,}\b"), REDACTION_TOKEN),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"), REDACTION_TOKEN),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), REDACTION_TOKEN),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"), REDACTION_TOKEN),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), REDACTION_TOKEN),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{33,40}"), REDACTION_TOKEN),
    (re.compile(r"\bSG\.[A-Za-z0-9_-]{16,32}\.[A-Za-z0-9_-]{16,64}\b"), REDACTION_TOKEN),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}\b"), REDACTION_TOKEN),
    (re.compile(r"\bSK[0-9a-fA-F]{32}\b"), REDACTION_TOKEN),
    (re.compile(r"\bLTAI[A-Za-z0-9]{12,20}\b"), REDACTION_TOKEN),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"), REDACTION_TOKEN),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL), REDACTION_TOKEN),
    (re.compile(r"(?i)(://[^:\s/@]{2,}):([^@\s/]{4,})@"), r"\1:" + REDACTION_TOKEN + "@"),
]

# Entropy fallback. Only applied to lines that already mention a credential, so
# commit hashes and request ids on ordinary lines are left alone.
# Broader than _KEYWORD on purpose: any identifier ending in "key" counts as
# context here (MAPS_KEY, AZURE_STORAGE_KEY). That is only safe because the
# entropy pass additionally requires a 32+ character high-entropy token on the
# same line, which ordinary identifiers never satisfy.
SECRET_CONTEXT_RE = re.compile(r"(?i)(" + _KEYWORD + r"|[A-Za-z0-9_-]*key\b|\bauth\b)")
ENTROPY_CANDIDATE_RE = re.compile(r"[A-Za-z0-9+/_-]{32,}={0,2}")
ENTROPY_THRESHOLD = 3.5

# Retained for callers that only need the aggregate pattern list.
SECRET_PATTERNS: list[re.Pattern[str]] = [KEY_VALUE_RE] + [pattern for pattern, _ in PROVIDER_PATTERNS]


def shannon_entropy(value: str) -> float:
    """Return the Shannon entropy, in bits per character, of a string."""
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _looks_like_code_reference(value: str, *, quoted: bool, line: str) -> bool:
    """Return whether a captured value is a code expression rather than a literal."""
    if CODE_REFERENCE_RE.match(value) or ENV_LOOKUP_RE.match(value):
        return True
    if quoted or not IDENTIFIER_RE.match(value):
        return False
    # Unquoted bare identifier: a reference only when it sits inside a call or a
    # dict/set literal. Otherwise it is a shell or dotenv style assignment.
    return "(" in line or "{" in line


def is_placeholder(value: str) -> bool:
    """Return whether a value is an obvious documentation placeholder."""
    return bool(PLACEHOLDER_RE.match(value))


def _redact_key_value(match: re.Match[str]) -> str:
    value = match.group("value")
    quote = match.group("quote") or ""
    line = _enclosing_line(match.string, match.start())
    if (REDACTION_TOKEN in value or is_placeholder(value)
            or _looks_like_code_reference(value, quoted=bool(quote), line=line)):
        return match.group(0)
    return f"{match.group('key')}{match.group('sep')}{quote}{REDACTION_TOKEN}{quote}"


def _enclosing_line(text: str, index: int) -> str:
    start = text.rfind("\n", 0, index) + 1
    end = text.find("\n", index)
    return text[start:] if end == -1 else text[start:end]


def _redact_entropy(text: str) -> tuple[str, int]:
    """Redact long high-entropy tokens on lines that mention a credential."""
    total = 0
    out_lines = []
    for line in text.split("\n"):
        if REDACTION_TOKEN in line or not SECRET_CONTEXT_RE.search(line):
            out_lines.append(line)
            continue

        def repl(match: re.Match[str]) -> str:
            nonlocal total
            candidate = match.group(0)
            if shannon_entropy(candidate) < ENTROPY_THRESHOLD:
                return candidate
            total += 1
            return REDACTION_TOKEN

        out_lines.append(ENTROPY_CANDIDATE_RE.sub(repl, line))
    return "\n".join(out_lines), total


def contains_secret(text: str) -> bool:
    """Return whether text appears to contain a secret value."""
    if not text:
        return False
    _redacted, count = redact_text(text)
    return count > 0


def redact_text(text: str) -> tuple[str, int]:
    """Redact secret values from a string and return the number of replacements."""
    if not text:
        return text, 0

    redacted, total = KEY_VALUE_RE.subn(_redact_key_value, text)
    # subn counts attempted matches; recount the ones the guard let through.
    total = redacted.count(REDACTION_TOKEN) - text.count(REDACTION_TOKEN)

    for pattern, replacement in PROVIDER_PATTERNS:
        redacted, count = pattern.subn(replacement, redacted)
        total += count

    redacted, entropy_count = _redact_entropy(redacted)
    total += entropy_count

    return redacted, max(total, 0)


def redact_obj(value: Any) -> tuple[Any, int]:
    """Recursively redact strings inside a JSON-like object."""
    if value is None:
        return None, 0
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        total = 0
        out = []
        for item in value:
            redacted, count = redact_obj(item)
            total += count
            out.append(redacted)
        return out, total
    if isinstance(value, tuple):
        redacted, count = redact_obj(list(value))
        return tuple(redacted), count
    if isinstance(value, dict):
        total = 0
        out = {}
        for key, item in value.items():
            redacted_key, key_count = redact_obj(key)
            redacted_item, item_count = redact_obj(item)
            total += key_count + item_count
            out[redacted_key] = redacted_item
        return out, total
    if is_dataclass(value):
        total = 0
        updates = {}
        for key, item in value.__dict__.items():
            redacted, count = redact_obj(item)
            total += count
            updates[key] = redacted
        return replace(value, **updates), total
    return value, 0


def redact_json_text(value: Any) -> tuple[str, int]:
    """Return redacted pretty JSON for a JSON-like value."""
    redacted, count = redact_obj(value)
    return json.dumps(redacted, ensure_ascii=False, indent=2, sort_keys=True), count
