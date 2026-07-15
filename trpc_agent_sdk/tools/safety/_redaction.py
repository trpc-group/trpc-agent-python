"""Redaction of secrets, env values, and oversized evidence.

Redaction runs before any serialization (report JSON, audit JSONL, span
attributes, structured log fields). The redactor is registered with the
known environment values upfront so subsequent calls are O(snippets).
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from trpc_agent_sdk.tools.safety._models import EVIDENCE_MAX_CHARS, Evidence, ScriptLanguage

# Patterns for common secret formats. Order matters: longer/more specific
# patterns first so their redaction wins over generic ones.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_block", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----"
        r".*?-----END (?:RSA |EC |OPENSSH |PGP |DSA )?PRIVATE KEY-----",
        re.DOTALL | re.IGNORECASE,
    )),
    ("bearer_token", re.compile(
        r"\b(?:Bearer|Token)\s+[A-Za-z0-9._\-+/=]{8,}", re.IGNORECASE,
    )),
    ("api_key_slash", re.compile(
        r"\bsk-[A-Za-z0-9]{16,}\b",
    )),
    ("aws_access_key", re.compile(
        r"\bAKIA[0-9A-Z]{16}\b",
    )),
    ("aws_secret_key", re.compile(
        r"\b(?:aws_|aws_secret_access_key\s*[:=]\s*)[A-Za-z0-9/+=]{40}\b",
        re.IGNORECASE,
    )),
    ("jwt", re.compile(
        r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\b",
    )),
    ("github_token", re.compile(
        r"\b(?:gh[ps]_|github_pat_)[A-Za-z0-9]{16,}\b",
    )),
    ("generic_password_assign", re.compile(
        r"(?P<key>password|passwd|pwd|secret|api[_-]?key|access[_-]?token)"
        r"\s*[:=]\s*[\"\']?"
        r"(?P<value>[^\"\'\s,;)}\]]{4,})",
        re.IGNORECASE,
    )),
    ("hex_secret_32", re.compile(
        r"\b[0-9a-fA-F]{32,64}\b",
    )),
)

_PLACEHOLDER = "<REDACTED:{kind}:{digest}>"
_ENV_PLACEHOLDER = "<REDACTED:env:{digest}>"
_REDACTION_MARKER = "<REDACTED:"


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:8]


class Redactor:
    """Stateful redactor that knows the request's env values.

    The redactor never persists raw secrets: env values are hashed into a
    short digest so audit logs can correlate "same value" without learning
    the value itself.
    """

    def __init__(self, env_values: Iterable[str] = (), *,
                 evidence_max_chars: int = EVIDENCE_MAX_CHARS) -> None:
        self._evidence_max_chars = max(0, evidence_max_chars)
        # Sort longer first so we replace the longest secrets before shorter
        # substrings of the same value can match.
        values = sorted({v for v in env_values if v}, key=len, reverse=True)
        self._env_values = values
        self._active = False

    @property
    def active(self) -> bool:
        """Whether this redactor has replaced a secret in emitted evidence."""

        return self._active

    def redact(self, text: str) -> str:
        """Return a redacted copy of ``text``."""

        if not text:
            return text
        redacted = text
        for value in self._env_values:
            if value and value in redacted:
                placeholder = _ENV_PLACEHOLDER.format(digest=_digest(value))
                redacted = redacted.replace(value, placeholder)
                self._active = True
        for kind, pattern in _SECRET_PATTERNS:
            before = redacted
            redacted = _apply_pattern(redacted, kind, pattern)
            if redacted != before:
                self._active = True
        return redacted

    def truncate(self, text: str) -> str:
        """Bound the text length, keeping head and tail with a marker."""

        if len(text) <= self._evidence_max_chars:
            return text
        if self._evidence_max_chars <= 16:
            return text[: self._evidence_max_chars]
        keep = self._evidence_max_chars - 5
        head = keep // 2
        tail = keep - head
        return f"{text[:head]}…<+{len(text) - keep}>…{text[-tail:]}"

    def build_evidence(
        self,
        *,
        snippet: str,
        line: int = 0,
        column: int = 0,
        language: ScriptLanguage = ScriptLanguage.UNKNOWN,
        extras: dict[str, str] | None = None,
    ) -> Evidence:
        """Build a fully redacted, bounded :class:`Evidence`."""

        clean_extras = {k: self.truncate(self.redact(str(v)))
                        for k, v in (extras or {}).items()}
        return Evidence(
            snippet=self.truncate(self.redact(snippet)),
            line=max(0, int(line or 0)),
            column=max(0, int(column or 0)),
            language=language,
            extras=tuple(clean_extras.items()) and dict(clean_extras) or {},
        )


def _apply_pattern(text: str, kind: str, pattern: re.Pattern[str]) -> str:
    def _sub(match: re.Match[str]) -> str:
        if "value" in match.groupdict():
            value = match.group("value")
            key = match.groupdict().get("key", kind)
            placeholder = _PLACEHOLDER.format(
                kind=key.lower() if isinstance(key, str) else kind,
                digest=_digest(value),
            )
            return match.group(0).replace(value, placeholder)
        placeholder = _PLACEHOLDER.format(kind=kind, digest=_digest(match.group(0)))
        return placeholder

    return pattern.sub(_sub, text)


def contains_secret_literal(value: str) -> bool:
    """Return whether a string literal resembles a credential.

    This is intentionally shared with :class:`Redactor` so a literal that is
    blocked as a potential leak is guaranteed to be redacted in its evidence.
    """

    return any(pattern.search(value) is not None
               for _, pattern in _SECRET_PATTERNS)


def evidence_was_redacted(evidence: Evidence) -> bool:
    """Return whether serialized evidence contains a redaction placeholder."""

    if _REDACTION_MARKER in evidence.snippet:
        return True
    return any(_REDACTION_MARKER in value
               for value in evidence.extras.values())


def make_default_redactor(env_values: Iterable[str] = ()) -> Redactor:
    """Convenience constructor used by the guard."""

    return Redactor(env_values)
