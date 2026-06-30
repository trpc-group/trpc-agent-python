# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared regex patterns and text-level detection for the safety scanners.

Both the Python and Bash scanners reuse :func:`iter_text_findings` for the
regex-detectable subset of rules (rm -rf, curl|bash, package installs, sudo,
ssh/env reads, hard-coded secrets, fork bombs and network egress). The Python
scanner adds AST-specific detections on top.

Safety of the scanner itself (design doc 6.1):

- Every regex is compiled at import time; a bad pattern fails loudly here, not
  at scan time.
- Matching is done line by line and each line is truncated to the policy's
  ``max_line_length`` before any regex runs, bounding regex input length so a
  pathological line cannot drive catastrophic backtracking (ReDoS).
"""

from __future__ import annotations

import os
import re
from typing import Iterator
from typing import List
from typing import Tuple

from ..policy import SafetyPolicy

# A finding produced by text scanning: (rule_id, snippet, line_number).
TextHit = Tuple[str, str, int]

# --------------------------------------------------------------------------- #
# Compiled patterns. Kept deliberately simple (no nested quantifiers) to avoid
# catastrophic backtracking.
# --------------------------------------------------------------------------- #
RE_RM_RF = re.compile(r"\brm\s+(?:-[a-zA-Z]*\s+|--[a-z-]+\s+)*-?[a-zA-Z]*[rf][a-zA-Z]*\b")
RE_CURL_PIPE_SH = re.compile(r"\b(?:curl|wget)\b[^\n|]{0,400}\|\s*(?:sudo\s+)?(?:ba|z|d)?sh\b")
RE_PIP_INSTALL = re.compile(r"\bpip[23]?\s+install\b|\bpython[23]?\s+-m\s+pip\s+install\b")
RE_NPM_INSTALL = re.compile(r"\b(?:npm|yarn|pnpm)\s+(?:install|add|i)\b")
RE_SYS_INSTALL = re.compile(r"\b(?:apt|apt-get|yum|dnf|brew|apk|pacman)\s+(?:install|add|-S)\b")
RE_SUDO = re.compile(r"\bsudo\b")
RE_CHMOD_777 = re.compile(r"\bchmod\s+(?:-R\s+)?(?:0?777|a\+rwx)\b")
RE_FORK_BOMB = re.compile(r":\s*\(\s*\)\s*\{[^\n}]{0,40}\|[^\n}]{0,40}&[^\n}]{0,20}\}\s*;")

# Reading secret material.
RE_SSH_KEY = re.compile(r"(?:/\.ssh/|\bid_rsa\b|\bid_dsa\b|\bid_ecdsa\b|\bid_ed25519\b|authorized_keys)")
RE_ENV_FILE = re.compile(r"(?:(?<![\w.])\.env\b|/\.aws/|\.aws[/\\]credentials|/\.config/gcloud|"
                         r"\bsecrets?\.(?:json|ya?ml|ini)\b|\bcredentials\.(?:json|ini)\b)")

# Hard-coded credentials.
RE_PRIVATE_KEY_BLOCK = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
RE_AWS_AKID = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")
RE_SECRET_ASSIGN = re.compile(
    r"""(?ix)
    \b(?:api[_-]?key|secret(?:[_-]?key)?|access[_-]?token|auth[_-]?token|
    password|passwd|private[_-]?key|access[_-]?key)\b
    \s*[:=]\s*
    ['"][^'"\n]{6,}['"]
    """)

# Identifier name that looks like a secret (used by AST output-leak detection).
RE_SECRET_NAME = re.compile(
    r"(?i)^(?:.*_)?(?:api_?key|secret|secret_?key|access_?token|auth_?token|"
    r"password|passwd|pwd|private_?key|access_?key|credential|token)s?$")

# URLs and bare hosts following a downloader command.
RE_URL = re.compile(r"\bhttps?://([A-Za-z0-9._\-]+)")
RE_DOWNLOAD_HOST = re.compile(r"\b(?:curl|wget|nc|ncat|telnet)\s+(?:-[^\s]+\s+)*([A-Za-z0-9._\-]+\.[A-Za-z]{2,})")

# IPv4 literal.
RE_IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")

# Patterns whose matched text is masked in evidence to avoid secondary leakage.
_REDACT_PATTERNS = [RE_PRIVATE_KEY_BLOCK, RE_AWS_AKID, RE_SECRET_ASSIGN]


def is_internal_host(host: str) -> bool:
    """True if ``host`` is a private / loopback / link-local IPv4 address or one
    of the additional internal ranges the security policy denies (9/11/21/30)."""
    host = (host or "").strip().strip("[]")
    m = RE_IPV4.match(host)
    if not m:
        return False
    try:
        a, b, c, d = (int(x) for x in m.groups())
    except ValueError:
        return False
    if any(o > 255 for o in (a, b, c, d)):
        return False
    if a in (0, 9, 10, 11, 21, 30, 127):  # 0/loopback + extra denied ranges
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 169 and b == 254:  # link-local
        return True
    return False


def _classify_host(host: str, policy: SafetyPolicy) -> str | None:
    """Return the rule id for an egress host, or None if it is allow-listed."""
    host = (host or "").strip().lower()
    if not host:
        return None
    if is_internal_host(host):
        return "NET_INTERNAL_IP"
    if policy.is_domain_allowed(host):
        return None
    return "NET_EGRESS_NON_ALLOWLIST"


def iter_text_findings(text: str, policy: SafetyPolicy) -> Iterator[TextHit]:
    """Yield ``(rule_id, snippet, line)`` for every regex-detectable rule hit.

    Scans line by line; each line is truncated to ``policy.scan_limits``'s
    ``max_line_length`` before matching (ReDoS guard).
    """
    max_line = policy.scan_limits.max_line_length
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line[:max_line]
        if not line.strip():
            continue

        # Single-match rules: (regex, rule_id).
        for regex, rule_id in (
            (RE_RM_RF, "FILE_RM_RF"),
            (RE_CURL_PIPE_SH, "PKG_CURL_PIPE_SH"),
            (RE_PIP_INSTALL, "PKG_PIP_INSTALL"),
            (RE_NPM_INSTALL, "PKG_NPM_INSTALL"),
            (RE_SYS_INSTALL, "PKG_SYS_INSTALL"),
            (RE_SUDO, "PRIV_SUDO"),
            (RE_CHMOD_777, "PRIV_CHMOD_777"),
            (RE_FORK_BOMB, "RES_FORK_BOMB"),
            (RE_SSH_KEY, "SECRET_READ_SSH"),
            (RE_ENV_FILE, "SECRET_READ_ENV"),
            (RE_PRIVATE_KEY_BLOCK, "SECRET_HARDCODED"),
            (RE_AWS_AKID, "SECRET_HARDCODED"),
            (RE_SECRET_ASSIGN, "SECRET_HARDCODED"),
        ):
            if regex.search(line):
                yield (rule_id, line.strip(), lineno)

        # Network egress: URLs and bare download hosts.
        seen_hosts: set[str] = set()
        for m in RE_URL.finditer(line):
            host = m.group(1)
            if host in seen_hosts:
                continue
            seen_hosts.add(host)
            rule_id = _classify_host(host, policy)
            if rule_id:
                yield (rule_id, line.strip(), lineno)
        for m in RE_DOWNLOAD_HOST.finditer(line):
            host = m.group(1)
            if host in seen_hosts:
                continue
            seen_hosts.add(host)
            rule_id = _classify_host(host, policy)
            if rule_id:
                yield (rule_id, line.strip(), lineno)


# Forbidden path prefixes that map to FILE_OVERWRITE_DEVICE (vs the generic
# FILE_FORBIDDEN_PATH) because writing there can brick the host.
_DEVICE_PREFIXES = ("/dev", "/proc", "/sys")
# A path that is too broad to match safely (every absolute path contains it);
# destructive use of the root is already covered by FILE_RM_RF.
_TOO_BROAD = {"", "/", "."}
# Characters that may legally terminate a path token in source/shell text. Used
# to require a boundary after the path so "/etc" does not match "/etcd/data".
_PATH_BOUNDARY = r"(?=$|[/\s\"')\]}>,;:|&])"


def _forbidden_path_matchers(policy: SafetyPolicy) -> List[Tuple[str, "re.Pattern[str]"]]:
    """Compile one matcher per configured forbidden path (acceptance req. 6).

    Each path in ``policy.forbidden_paths`` becomes a boundary-anchored regex so
    that operators can add/remove forbidden paths in the YAML policy and change
    scan results with **no code change**. ``~`` is expanded so both the literal
    (``~/.ssh``) and the resolved home form are caught. ``/`` and other overly
    broad entries are skipped to avoid pathological false positives.
    """
    matchers: List[Tuple[str, "re.Pattern[str]"]] = []
    seen: set[str] = set()
    for raw in policy.forbidden_paths:
        original = (raw or "").strip()
        if original in _TOO_BROAD:
            continue
        rule_id = ("FILE_OVERWRITE_DEVICE"
                   if original.startswith(_DEVICE_PREFIXES) else "FILE_FORBIDDEN_PATH")
        variants = {original}
        expanded = os.path.expanduser(original)
        if expanded != original and expanded not in _TOO_BROAD:
            variants.add(expanded)
        for variant in variants:
            if variant in seen:
                continue
            seen.add(variant)
            try:
                pattern = re.compile(r"(?<![\w.~])" + re.escape(variant) + _PATH_BOUNDARY)
            except re.error:
                continue
            matchers.append((rule_id, pattern))
    return matchers


def iter_forbidden_path_findings(text: str, policy: SafetyPolicy) -> Iterator[TextHit]:
    """Yield ``(rule_id, snippet, line)`` for every configured forbidden path hit.

    This is what makes ``forbidden_paths`` in the policy file actually enforced;
    the matchers are built from the live policy, so editing the policy changes
    behaviour without touching code.
    """
    matchers = _forbidden_path_matchers(policy)
    if not matchers:
        return
    max_line = policy.scan_limits.max_line_length
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line[:max_line]
        if not line.strip():
            continue
        for rule_id, pattern in matchers:
            if pattern.search(line):
                yield (rule_id, line.strip(), lineno)


def redact_text(text: str, policy: SafetyPolicy) -> tuple[str, bool]:
    """Mask secret-looking substrings in ``text``.

    Returns ``(masked_text, changed)`` where ``changed`` is True if anything was
    masked. Honours ``policy.redact`` (toggle, mask string and extra patterns).
    """
    if not policy.redact.enabled:
        return text, False
    mask = policy.redact.mask
    changed = False
    result = text
    patterns = list(_REDACT_PATTERNS)
    for extra in policy.redact.patterns:
        try:
            patterns.append(re.compile(extra))
        except re.error:
            continue
    for regex in patterns:
        new_result, n = regex.subn(mask, result)
        if n:
            changed = True
            result = new_result
    return result, changed
