# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shell-token-based Bash script scanner for the Tool Script Safety Guard.

Whereas the regex-based rules in ``_rules.py`` match patterns against raw text,
this module tokenizes Bash source with ``shlex.shlex`` (punctuation-aware) and
analyses the token stream with quote-state tracking.  This eliminates a large
class of false positives where a "dangerous" pattern appears inside a string
literal or comment.

Key features:
    * Quote-state tracking (``'…'``, ``"…"``, ``\\`` escaping).
    * Command-name extraction with argument collection.
    * ``rm -rf`` detection via token analysis (catches ``rm -r -f``, ``/bin/rm -rf``).
    * Fork bomb detection with generalised regex.
    * Background operator ``&`` vs ``&&`` distinction.
    * Heredoc and ``$()`` nesting awareness.
    * Long-sleep duration parsing with unit support.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set

# ---------------------------------------------------------------------------
# Finding dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BashScanFinding:
    """A single observation from the Bash scanner.

    Attributes:
        kind: One of ``"command"``, ``"rm_rf"``, ``"pipe"``, ``"redirect"``,
              ``"background"``, ``"fork_bomb"``, ``"install"``, ``"sudo"``,
              ``"curl"``, ``"wget"``, ``"eval"``, ``"heredoc"``,
              ``"long_sleep"``, ``"secret_ref"``.
        command: The first token (command name) for command-like findings.
        args: Remaining tokens (joined with space).
        line_number: 1-based source line.
        evidence: Relevant source snippet (truncated).
        extra: Additional structured data.
    """

    kind: str
    command: str = ""
    args: str = ""
    line_number: int = 0
    evidence: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════════════════
# Known-command sets
# ═══════════════════════════════════════════════════════════════════════════

_NETWORK_COMMANDS: Set[str] = {
    "curl",
    "wget",
    "nc",
    "ncat",
    "netcat",
    "telnet",
    "ssh",
    "scp",
    "sftp",
    "rsync",
    "ftp",
    "socat",
    "aria2c",
    "axel",
}

_INSTALL_COMMANDS: Set[str] = {
    "pip",
    "pip3",
    "pipx",
    "npm",
    "yarn",
    "pnpm",
    "npx",
    "apt",
    "apt-get",
    "yum",
    "dnf",
    "zypper",
    "pacman",
    "brew",
    "cargo",
    "go",
    "gem",
}

_PRIVILEGE_COMMANDS: Set[str] = {
    "sudo",
    "su",
    "doas",
    "pkexec",
    "chroot",
}

_DESTRUCTIVE_COMMANDS: Set[str] = {
    "mkfs",
    "mkfs.ext4",
    "mkfs.xfs",
    "mkfs.btrfs",
}

_FILE_READ_COMMANDS: Set[str] = {
    "cat",
    "head",
    "tail",
    "grep",
    "awk",
    "sed",
    "less",
    "more",
    "strings",
    "od",
    "xxd",
    "hexdump",
}

_FILE_WRITE_COMMANDS: Set[str] = {
    "tee",
    "dd",
}

_DYNAMIC_COMMANDS: Set[str] = {
    "eval",
    "exec",
    "source",
    ".",
}

# Commands whose sub-command is also checked
_SUBCOMMAND_MAP: Dict[str, Set[str]] = {
    "pip": {"install", "uninstall", "download"},
    "pip3": {"install", "uninstall", "download"},
    "npm": {"install", "i", "add", "update"},
    "apt": {"install", "remove", "purge"},
    "apt-get": {"install", "remove", "purge"},
    "brew": {"install", "uninstall", "upgrade"},
    "cargo": {"install", "uninstall"},
    "go": {"install", "get"},
    "gem": {"install", "uninstall"},
}

# ═══════════════════════════════════════════════════════════════════════════
# Bash Scanner
# ═══════════════════════════════════════════════════════════════════════════


class BashScanner:
    """Tokenize Bash source and collect security-relevant observations.

    Args:
        source: Raw Bash source text.
        max_lines: Soft limit for scanning.
    """

    def __init__(self, source: str, *, max_lines: int = 500) -> None:
        self._source = source
        self._lines = source.splitlines()
        self._max_lines = max_lines
        self._findings: List[BashScanFinding] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> List[BashScanFinding]:
        """Run all analyses and return findings."""
        if len(self._lines) > self._max_lines:
            self._findings.append(
                BashScanFinding(
                    kind="oversized",
                    evidence=f"{len(self._lines)} lines exceeds {self._max_lines}",
                ))

        # 1. Line-by-line token analysis
        self._scan_lines()

        # 2. Cross-line fork bomb detection
        self._check_fork_bomb()

        # 3. Heredoc detection (before stripping, since heredocs span lines)
        self._check_heredocs()

        # 4. Long sleep detection
        self._check_long_sleeps()

        # 5. Secret reference in output
        self._check_secret_refs()

        return self._findings

    # ------------------------------------------------------------------
    # Line-by-line scanner
    # ------------------------------------------------------------------

    def _scan_lines(self) -> None:
        """Tokenize each line and dispatch per-command checks."""
        for line_no, raw_line in enumerate(self._lines, start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            # Strip inline comments (safely: outside quotes)
            clean = _strip_inline_comment(stripped)
            if not clean:
                continue

            # Tokenize the line
            tokens = _tokenize_line(clean)
            if not tokens:
                continue

            # Check shebang
            if line_no == 1 and clean.startswith("#!"):
                continue

            # Check redirects
            self._check_redirects(line_no, raw_line, tokens)

            # Check pipes and background
            self._check_operators(line_no, raw_line, tokens)

            # Command analysis
            cmd = tokens[0]
            # Skip variable assignments (FOO=bar cmd)
            if "=" in cmd and "=" == cmd.split(None, 1)[0][-1:]:
                # VAR=val — skip to next token
                if len(tokens) > 1:
                    cmd = tokens[1]
                    tokens = tokens[1:]
                else:
                    continue

            cmd_lower = cmd.lower()
            args = tokens[1:]
            args_str = " ".join(args)

            # --- Dispatch ---
            if cmd_lower == "rm":
                self._check_rm(line_no, raw_line, tokens, args)
            elif cmd_lower in _NETWORK_COMMANDS:
                self._findings.append(
                    BashScanFinding(
                        kind=cmd_lower,
                        command=cmd_lower,
                        args=args_str,
                        line_number=line_no,
                        evidence=raw_line.strip()[:300],
                    ))
            elif cmd_lower in _PRIVILEGE_COMMANDS:
                self._findings.append(
                    BashScanFinding(
                        kind="sudo" if cmd_lower != "chroot" else "sudo",
                        command=cmd_lower,
                        args=args_str,
                        line_number=line_no,
                        evidence=raw_line.strip()[:300],
                        extra={"privilege_command": cmd_lower},
                    ))
            elif cmd_lower in _INSTALL_COMMANDS:
                sub = args[0].lower() if args else ""
                is_install = sub in _SUBCOMMAND_MAP.get(cmd_lower, set())
                if is_install or cmd_lower in ("pip", "pip3", "pipx"):
                    self._findings.append(
                        BashScanFinding(
                            kind="install",
                            command=cmd_lower,
                            args=args_str,
                            line_number=line_no,
                            evidence=raw_line.strip()[:300],
                            extra={
                                "package_manager": cmd_lower,
                                "subcommand": sub if is_install else "",
                            },
                        ))
            elif cmd_lower in _DESTRUCTIVE_COMMANDS:
                self._findings.append(
                    BashScanFinding(
                        kind="command",
                        command=cmd_lower,
                        args=args_str,
                        line_number=line_no,
                        evidence=raw_line.strip()[:300],
                        extra={"risk": "destructive"},
                    ))
            elif cmd_lower in _DYNAMIC_COMMANDS:
                self._findings.append(
                    BashScanFinding(
                        kind="eval",
                        command=cmd_lower,
                        args=args_str,
                        line_number=line_no,
                        evidence=raw_line.strip()[:300],
                    ))
            elif cmd_lower in _FILE_READ_COMMANDS:
                # Check if reads a sensitive path
                path_args = [a for a in args if not a.startswith("-")]
                for pa in path_args:
                    if _is_sensitive_path(pa):
                        self._findings.append(
                            BashScanFinding(
                                kind="command",
                                command=cmd_lower,
                                args=args_str,
                                line_number=line_no,
                                evidence=raw_line.strip()[:300],
                                extra={
                                    "risk": "sensitive_file_read",
                                    "path": pa
                                },
                            ))
                        break
            elif cmd_lower in _FILE_WRITE_COMMANDS and cmd_lower == "dd":
                self._check_dd(line_no, raw_line, args)

    # ------------------------------------------------------------------
    # Specific checks
    # ------------------------------------------------------------------

    def _check_rm(self, line_no: int, raw_line: str, tokens: List[str], args: List[str]) -> None:
        """Check for recursive-delete.  Flags both ``rm -rf`` and ``rm -r -f``."""
        all_tokens = set(tokens)
        # Handles combined flags: -rf, -fr, -r, -R, --recursive
        has_r = any(("r" in t.replace("-", "").lower() and not t.startswith("--")) or t in ("-r", "-R", "--recursive")
                    for t in tokens if t.startswith("-"))
        # Handles combined flags: -rf, -fr, -f, --force
        has_f = any(("f" in t.replace("-", "").lower() and not t.startswith("--")) or t in ("-f", "--force")
                    for t in tokens if t.startswith("-"))

        # Check recursive via long option
        has_recursive = "--recursive" in all_tokens

        if (has_r or has_recursive) and has_f:
            target = args[-1] if args else "?"
            self._findings.append(
                BashScanFinding(
                    kind="rm_rf",
                    command="rm",
                    args=" ".join(args),
                    line_number=line_no,
                    evidence=raw_line.strip()[:300],
                    extra={
                        "target": target,
                        "recursive": True,
                        "force": True
                    },
                ))
        elif has_r or has_recursive:
            target = args[-1] if args else "?"
            if target and (target.startswith("/") or _is_sensitive_path(target)):
                self._findings.append(
                    BashScanFinding(
                        kind="rm_rf",
                        command="rm",
                        args=" ".join(args),
                        line_number=line_no,
                        evidence=raw_line.strip()[:300],
                        extra={
                            "target": target,
                            "recursive": True,
                            "force": False,
                            "sensitive_target": True,
                        },
                    ))

    def _check_redirects(self, line_no: int, raw_line: str, tokens: List[str]) -> None:
        """Detect write redirects to sensitive paths."""
        # Look for > or >> patterns
        for i, t in enumerate(tokens):
            if t in (">", ">>", ">" + ">") and i + 1 < len(tokens):
                target = tokens[i + 1]
                if _is_sensitive_path(target) or target.startswith("/dev/"):
                    self._findings.append(
                        BashScanFinding(
                            kind="redirect",
                            command=">",
                            args=target,
                            line_number=line_no,
                            evidence=raw_line.strip()[:300],
                            extra={"target": target},
                        ))
            # Also handle inline redirect: 2>/dev/null, >/etc/passwd
            if ">" in t and len(t) > 1:
                parts = t.split(">", 1)
                target = parts[1].strip()
                if target and (_is_sensitive_path(target) or target.startswith("/dev/sd")):
                    self._findings.append(
                        BashScanFinding(
                            kind="redirect",
                            command=">",
                            args=target,
                            line_number=line_no,
                            evidence=raw_line.strip()[:300],
                            extra={"target": target},
                        ))

    def _check_operators(self, line_no: int, raw_line: str, tokens: List[str]) -> None:
        """Detect pipes and background operators.

        Distinguishes single ``|`` from ``||`` (logical OR) and single ``&``
        from ``&&`` (logical AND), ``|&``, and ``>&`` so that shell
        short-circuit operators do not produce false pipe/background findings.
        """
        raw_tokens = _tokenize_line(raw_line)

        # Pipe: single | not adjacent to another | (i.e. not ||)
        for i, t in enumerate(raw_tokens):
            if t == "|":
                # Skip if this | is part of || or |&
                if (i > 0 and raw_tokens[i - 1] == "|") or (i + 1 < len(raw_tokens) and raw_tokens[i + 1] == "|"):
                    continue
                self._findings.append(
                    BashScanFinding(
                        kind="pipe",
                        command="|",
                        line_number=line_no,
                        evidence=raw_line.strip()[:300],
                    ))
                break

        # Background: standalone & (not &&, not |&, not >&)
        for i, t in enumerate(raw_tokens):
            if t == "&":
                # Skip if part of &&, |&, or >&
                if (i > 0 and raw_tokens[i - 1] in ("|", ">")) or (i > 0 and raw_tokens[i - 1] == "&") or (
                        i + 1 < len(raw_tokens) and raw_tokens[i + 1] == "&"):
                    continue
                self._findings.append(
                    BashScanFinding(
                        kind="background",
                        command="&",
                        line_number=line_no,
                        evidence=raw_line.strip()[:300],
                    ))
                break

    def _check_dd(self, line_no: int, raw_line: str, args: List[str]) -> None:
        """Parse dd arguments for output-device detection."""
        of_target = None
        bs_val = None
        count_val = None
        for a in args:
            if a.startswith("of="):
                of_target = a[3:]
            elif a.startswith("bs="):
                try:
                    bs_val = _parse_size(a[3:])
                except ValueError:
                    pass
            elif a.startswith("count="):
                try:
                    count_val = int(a[6:])
                except ValueError:
                    pass

        is_write_to_dev = of_target and of_target.startswith("/dev/")
        is_large_write = (bs_val and count_val and bs_val * count_val > 100 * 1024 * 1024)

        if is_write_to_dev:
            self._findings.append(
                BashScanFinding(
                    kind="command",
                    command="dd",
                    args=" ".join(args),
                    line_number=line_no,
                    evidence=raw_line.strip()[:300],
                    extra={
                        "risk": "device_write",
                        "of": of_target
                    },
                ))
        elif is_large_write:
            self._findings.append(
                BashScanFinding(
                    kind="command",
                    command="dd",
                    args=" ".join(args),
                    line_number=line_no,
                    evidence=raw_line.strip()[:300],
                    extra={
                        "risk": "large_write",
                        "estimated_bytes": bs_val * count_val
                    },
                ))

    def _check_fork_bomb(self) -> None:
        """Check the full source for fork bomb patterns."""
        # Literal :(){ :|:& };:
        literal_pattern = r":\s*\(\s*\)\s*\{\s*:\s*\|[^}]*\}"
        for m in re.finditer(literal_pattern, self._source):
            line_no = self._source[:m.start()].count("\n") + 1
            self._findings.append(
                BashScanFinding(
                    kind="fork_bomb",
                    command="fork_bomb",
                    line_number=line_no,
                    evidence=m.group(0)[:200],
                    extra={"pattern": "literal"},
                ))

        # Generalised: <name>(){ <name>|<name>& };<name>
        # This catches renamed variants
        generalized = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*\)\s*\{\s*\1\s*\|\s*\1\s*&[^}]*\}\s*;?\s*\1", )
        for m in generalized.finditer(self._source):
            line_no = self._source[:m.start()].count("\n") + 1
            self._findings.append(
                BashScanFinding(
                    kind="fork_bomb",
                    command="fork_bomb",
                    line_number=line_no,
                    evidence=m.group(0)[:200],
                    extra={
                        "pattern": "generalized",
                        "name": m.group(1)
                    },
                ))

    def _check_heredocs(self) -> None:
        """Detect heredoc with inline execution (e.g. ``python3 << EOF … EOF``)."""
        heredoc_re = re.compile(
            r"(python3?|bash|sh|perl|ruby)\s+<<\s*['\"]?(\w+)['\"]?",
            re.IGNORECASE,
        )
        for m in heredoc_re.finditer(self._source):
            line_no = self._source[:m.start()].count("\n") + 1
            self._findings.append(
                BashScanFinding(
                    kind="heredoc",
                    command=m.group(1),
                    line_number=line_no,
                    evidence=m.group(0),
                    extra={
                        "interpreter": m.group(1),
                        "delimiter": m.group(2)
                    },
                ))

    def _check_long_sleeps(self, default_threshold: int = 60) -> None:
        """Detect sleep commands with excessively long durations."""
        sleep_re = re.compile(r"sleep\s+(\d+)([smhd]?)", re.IGNORECASE)
        for m in sleep_re.finditer(self._source):
            value = int(m.group(1))
            unit = m.group(2).lower()
            seconds = _to_seconds(value, unit)
            if seconds > default_threshold:
                line_no = self._source[:m.start()].count("\n") + 1
                self._findings.append(
                    BashScanFinding(
                        kind="long_sleep",
                        command="sleep",
                        args=m.group(0),
                        line_number=line_no,
                        evidence=m.group(0),
                        extra={
                            "duration_seconds": seconds,
                            "threshold_seconds": default_threshold,
                        },
                    ))

    def _check_secret_refs(self) -> None:
        """Detect references to secret-like variable names in echo/print."""
        secret_var_re = re.compile(
            r"\b(echo|printf)\b.*\$(?:{)?\w*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH)\w*(?:})?",
            re.IGNORECASE,
        )
        for m in secret_var_re.finditer(self._source):
            line_no = self._source[:m.start()].count("\n") + 1
            self._findings.append(
                BashScanFinding(
                    kind="secret_ref",
                    command=m.group(1),
                    line_number=line_no,
                    evidence=m.group(0)[:200],
                    extra={"variable_ref": m.group(0)},
                ))


# ═══════════════════════════════════════════════════════════════════════════
# Public helpers — used by rules in _rules.py
# ═══════════════════════════════════════════════════════════════════════════


def scan_bash(source: str, *, max_lines: int = 500) -> List[BashScanFinding]:
    """Run the Bash scanner on *source* and return all findings."""
    scanner = BashScanner(source, max_lines=max_lines)
    return scanner.scan()


def has_bash_command(findings: List[BashScanFinding], command: str) -> bool:
    """Return True if *command* appears in findings."""
    cmd_lower = command.lower()
    return any(f.command.lower() == cmd_lower for f in findings)


def get_bash_network_commands(findings: List[BashScanFinding]) -> List[BashScanFinding]:
    """Return findings for network-related commands (curl, wget, etc.)."""
    return [f for f in findings if f.kind in _NETWORK_COMMANDS]


def get_bash_install_commands(findings: List[BashScanFinding]) -> List[BashScanFinding]:
    """Return findings for package-manager invocations."""
    return [f for f in findings if f.kind == "install"]


def get_bash_privilege_commands(findings: List[BashScanFinding]) -> List[BashScanFinding]:
    """Return findings for privilege escalation commands."""
    return [f for f in findings if f.kind == "sudo"]


def get_bash_rm_rf(findings: List[BashScanFinding]) -> List[BashScanFinding]:
    """Return recursive-delete findings."""
    return [f for f in findings if f.kind == "rm_rf"]


def get_bash_pipes(findings: List[BashScanFinding]) -> List[BashScanFinding]:
    """Return pipe findings."""
    return [f for f in findings if f.kind == "pipe"]


def get_bash_fork_bombs(findings: List[BashScanFinding]) -> List[BashScanFinding]:
    """Return fork bomb findings."""
    return [f for f in findings if f.kind == "fork_bomb"]


def get_bash_long_sleeps(findings: List[BashScanFinding]) -> List[BashScanFinding]:
    """Return long-sleep findings."""
    return [f for f in findings if f.kind == "long_sleep"]


def get_bash_secret_refs(findings: List[BashScanFinding]) -> List[BashScanFinding]:
    """Return secret-reference findings."""
    return [f for f in findings if f.kind == "secret_ref"]


def get_bash_dynamic_exec(findings: List[BashScanFinding]) -> List[BashScanFinding]:
    """Return eval/source findings."""
    return [f for f in findings if f.kind == "eval"]


# ═══════════════════════════════════════════════════════════════════════════
# Tokenizer helpers
# ═══════════════════════════════════════════════════════════════════════════


def _tokenize_line(line: str) -> List[str]:
    """Tokenize a single line of Bash with shlex, preserving operators."""
    if not line.strip():
        return []
    try:
        lexer = shlex.shlex(line, posix=True, punctuation_chars="|&;<>()$`\\\"'")
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        # Unclosed quote or other shlex issue — best-effort: split by whitespace
        return line.split()


def _strip_inline_comment(line: str) -> str:
    """Strip an inline comment (# ...) only when outside quotes.

    This is a conservative filter: if it cannot determine quote state it
    returns the original line unchanged to avoid false negatives.
    """
    result: List[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line):
            result.append(ch)
            result.append(line[i + 1])
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            result.append(ch)
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            result.append(ch)
            i += 1
            continue
        if ch == "#" and not in_single and not in_double:
            # Check that # is preceded by whitespace or start-of-line
            prev = line[i - 1] if i > 0 else " "
            if prev in (" ", "\t", ""):
                break  # rest is comment
        result.append(ch)
        i += 1
    return "".join(result).strip()


# ═══════════════════════════════════════════════════════════════════════════
# Misc helpers
# ═══════════════════════════════════════════════════════════════════════════

_SENSITIVE_PATHS_RE = re.compile(
    r"(?:/etc/(?:shadow|passwd|sudoers|hosts)|"
    r"~?/\.ssh|~?/\.gnupg|~?/\.aws|~?/\.gcloud|~?/\.azure|"
    r"\.env|\.pem|id_rsa|id_ed25519|id_ecdsa|"
    r"/proc/(?:self|\\d+)/(?:mem|cmdline|environ)|"
    r"/var/run/docker\.sock)",
    re.IGNORECASE,
)


def _is_sensitive_path(path: str) -> bool:
    """Return True if *path* looks like a sensitive/credential file path."""
    return bool(_SENSITIVE_PATHS_RE.search(path))


_SIZE_UNITS: Dict[str, int] = {
    "": 512,  # default dd block size
    "b": 1,
    "k": 1024,
    "m": 1024 * 1024,
    "g": 1024 * 1024 * 1024,
    "kb": 1000,
    "mb": 1000 * 1000,
    "gb": 1000 * 1000 * 1000,
}


def _parse_size(size_str: str) -> int:
    """Parse a size string like '4K', '1M', '512' into bytes."""
    size_str = size_str.strip().lower()
    num_part = re.match(r"(\d+)", size_str)
    if not num_part:
        raise ValueError(f"Cannot parse size: {size_str}")
    num = int(num_part.group(1))
    unit = size_str[num_part.end():]
    multiplier = _SIZE_UNITS.get(unit, 1)
    return num * multiplier


def _to_seconds(value: int, unit: str) -> int:
    """Convert a sleep duration with optional unit to seconds."""
    multipliers = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}
    return value * multipliers.get(unit, 1)
