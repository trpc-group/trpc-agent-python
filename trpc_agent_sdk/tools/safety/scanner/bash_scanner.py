"""Bash script scanning utilities using compiled regex patterns.

Bash has no standard AST library, so detection relies on regex pattern matching.
This module provides reusable pattern compilation and line-by-line scanning helpers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PatternMatch:
    """A single regex match result with context."""

    line_number: int
    """1-based line number where the match occurred."""

    line_content: str
    """The full line content (stripped of leading/trailing whitespace)."""

    matched_text: str
    """The actual text that matched the pattern."""

    pattern_name: str
    """Name/identifier of the pattern that matched."""


class CompiledPatternSet:
    """A set of named, pre-compiled regex patterns for efficient scanning.

    Patterns are compiled once at construction time and reused across scans.
    """

    def __init__(self, patterns: dict[str, str], flags: int = re.IGNORECASE) -> None:
        """Initialize with a dict of {name: regex_pattern_string}.

        Args:
            patterns: Mapping from pattern name to regex string.
            flags: Regex compilation flags. Default is case-insensitive.
        """
        self._patterns: list[tuple[str, re.Pattern[str]]] = []
        for name, pattern_str in patterns.items():
            self._patterns.append((name, re.compile(pattern_str, flags)))

    @property
    def count(self) -> int:
        """Number of patterns in the set."""
        return len(self._patterns)

    def match_line(self, line: str) -> list[tuple[str, re.Match[str]]]:
        """Match a single line against all patterns.

        Returns list of (pattern_name, match_object) for all matching patterns.
        """
        results: list[tuple[str, re.Match[str]]] = []
        for name, compiled in self._patterns:
            m = compiled.search(line)
            if m:
                results.append((name, m))
        return results


def is_comment_line(line: str) -> bool:
    """Check if a line is a bash comment (ignoring leading whitespace).

    Handles:
    - Lines starting with #
    - Empty lines (treated as non-comment, non-code)
    """
    stripped = line.strip()
    if not stripped:
        return False  # Empty lines are not comments
    return stripped.startswith("#")


def strip_inline_comment(line: str) -> str:
    """Remove inline comment from a bash line.

    Handles the common case of `command # comment`.
    Does NOT handle # inside quotes (conservative: if ambiguous, keep it).
    """
    # Simple heuristic: find # not inside quotes
    in_single_quote = False
    in_double_quote = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif ch == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif ch == "#" and not in_single_quote and not in_double_quote:
            return line[:i].rstrip()
    return line


def scan_lines(
    source: str,
    patterns: CompiledPatternSet,
    skip_comments: bool = True,
) -> list[PatternMatch]:
    """Scan source code line by line against a pattern set.

    Args:
        source: Full bash script source code.
        patterns: Compiled pattern set to match against.
        skip_comments: If True, skip lines that are pure comments.

    Returns:
        List of PatternMatch objects for all matches found.
    """
    results: list[PatternMatch] = []
    for line_num, raw_line in enumerate(source.splitlines(), start=1):
        if skip_comments and is_comment_line(raw_line):
            continue

        # Strip inline comments for matching (but not if the whole line is a comment
        # and we're explicitly including comments)
        if is_comment_line(raw_line):
            # Whole line is a comment — use it as-is when skip_comments=False
            effective_line = raw_line
        else:
            effective_line = strip_inline_comment(raw_line)
        if not effective_line.strip():
            continue

        for pattern_name, match in patterns.match_line(effective_line):
            results.append(
                PatternMatch(
                    line_number=line_num,
                    line_content=raw_line.strip(),
                    matched_text=match.group(0),
                    pattern_name=pattern_name,
                ))
    return results


def extract_urls_from_line(line: str) -> list[str]:
    """Extract URL-like strings from a line (http/https/ftp)."""
    url_pattern = re.compile(r'https?://[^\s"\'<>|;`]+|ftp://[^\s"\'<>|;`]+')
    return url_pattern.findall(line)


def extract_domain_from_url(url: str) -> Optional[str]:
    """Extract domain from a URL string.

    Returns None if the URL is malformed or has no recognizable host.
    """
    # Strip protocol
    for prefix in ("https://", "http://", "ftp://"):
        if url.startswith(prefix):
            url = url[len(prefix):]
            break
    # Strip user:pass@ if present
    if "@" in url:
        url = url.split("@", 1)[-1]
    # Take everything before first / or : (port)
    host = url.split("/")[0].split(":")[0]
    if not host or "." not in host:
        return None
    return host.lower()
