# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared matching constants and compiled patterns for safety scanners."""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Sensitive path patterns
# ---------------------------------------------------------------------------
SENSITIVE_PATHS = [
    "/etc",
    "/root",
    "/proc",
    "/sys",
    "/boot",
    "/dev",
    "~/.ssh",
    "~/.aws",
    "~/.kube",
    "~/.config",
    ".env",
    ".npmrc",
    ".pypirc",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credential",
    "secrets",
    "secret",
    "token",
    "password",
]

_SENSITIVE_SUFFIXES = {".pem", ".key", ".crt", ".cer", ".p12", ".pfx"}

# ---------------------------------------------------------------------------
# Sensitive environment variable name patterns
# ---------------------------------------------------------------------------
SENSITIVE_ENV_KEYS = re.compile(r"(?i)(api[_-]?key|token|password|passwd|secret|private[_-]?key|credential|auth)", )

# ---------------------------------------------------------------------------
# Secret value detection regex (for sanitization)
# ---------------------------------------------------------------------------
SECRET_VALUE_RE = re.compile(
    r"(?i)(sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_]{12,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----)", )

SECRET_KEY_VALUE_RE = re.compile(r"(?i)(api[_-]?key|token|password|passwd|secret|private[_-]?key)\s*[:=]\s*\S+", )

# ---------------------------------------------------------------------------
# Python-specific patterns
# ---------------------------------------------------------------------------

PYTHON_DANGEROUS_FILE_CALLS = {
    "open": {
        "risk": "high",
        "rule_id": "R001_FILE_DANGEROUS_OPEN"
    },
    "Path.open": {
        "risk": "high",
        "rule_id": "R001_FILE_DANGEROUS_OPEN"
    },
    "read_text": {
        "risk": "medium",
        "rule_id": "R001_FILE_READ"
    },
    "write_text": {
        "risk": "high",
        "rule_id": "R001_FILE_WRITE"
    },
    "read_bytes": {
        "risk": "medium",
        "rule_id": "R001_FILE_READ"
    },
    "write_bytes": {
        "risk": "high",
        "rule_id": "R001_FILE_WRITE"
    },
}

PYTHON_DELETE_CALLS = {
    "shutil.rmtree": "R001_RECURSIVE_DELETE",
    "os.remove": "R001_FILE_DELETE",
    "os.unlink": "R001_FILE_DELETE",
    "Path.unlink": "R001_FILE_DELETE",
}

PYTHON_NETWORK_IMPORTS = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib.request",
    "urllib3",
    "socket",
    "websocket",
    "websockets",
}

PYTHON_NETWORK_CALLS = {
    "requests.get": "R002_REQUESTS_EXTERNAL_REQUEST",
    "requests.post": "R002_REQUESTS_EXTERNAL_REQUEST",
    "requests.put": "R002_REQUESTS_EXTERNAL_REQUEST",
    "requests.delete": "R002_REQUESTS_EXTERNAL_REQUEST",
    "requests.Session": "R002_REQUESTS_EXTERNAL_REQUEST",
    "httpx.get": "R002_REQUESTS_EXTERNAL_REQUEST",
    "httpx.post": "R002_REQUESTS_EXTERNAL_REQUEST",
    "httpx.Client": "R002_REQUESTS_EXTERNAL_REQUEST",
    "aiohttp.ClientSession": "R002_AIOHTTP_EXTERNAL_REQUEST",
    "urllib.request.urlopen": "R002_REQUESTS_EXTERNAL_REQUEST",
    "socket.create_connection": "R002_SOCKET_EXTERNAL_CONNECTION",
    "socket.connect": "R002_SOCKET_EXTERNAL_CONNECTION",
}

PYTHON_SYSTEM_CALLS = {
    "subprocess.call": "R003_SUBPROCESS_EXECUTION",
    "subprocess.run": "R003_SUBPROCESS_EXECUTION",
    "subprocess.Popen": "R003_SUBPROCESS_EXECUTION",
    "subprocess.check_call": "R003_SUBPROCESS_EXECUTION",
    "subprocess.check_output": "R003_SUBPROCESS_EXECUTION",
    "os.system": "R003_OS_SYSTEM_EXECUTION",
    "os.popen": "R003_OS_SYSTEM_EXECUTION",
    "pty.spawn": "R003_OS_SYSTEM_EXECUTION",
}

PYTHON_DYNAMIC_EXEC_CALLS = {
    "eval": "R003_DYNAMIC_CODE_EXECUTION",
    "exec": "R003_DYNAMIC_CODE_EXECUTION",
    "compile": "R003_DYNAMIC_CODE_EXECUTION",
    "__import__": "R003_DYNAMIC_IMPORT",
}

PYTHON_INSTALL_PATTERNS = [
    (re.compile(r"pip\s+install"), "R004_PIP_INSTALL"),
    (re.compile(r"python\s+-m\s+pip\s+install"), "R004_PIP_INSTALL"),
    (re.compile(r"pip3\s+install"), "R004_PIP_INSTALL"),
    (re.compile(r"npm\s+install"), "R004_NPM_INSTALL"),
    (re.compile(r"yarn\s+add"), "R004_NPM_INSTALL"),
    (re.compile(r"pnpm\s+add"), "R004_NPM_INSTALL"),
    (re.compile(r"apt\s+install"), "R004_APT_INSTALL"),
    (re.compile(r"apt-get\s+install"), "R004_APT_INSTALL"),
    (re.compile(r"brew\s+install"), "R004_APT_INSTALL"),
    (re.compile(r"poetry\s+add"), "R004_PIP_INSTALL"),
    (re.compile(r"yum\s+install"), "R004_YUM_INSTALL"),
]

PYTHON_RESOURCE_PATTERNS = [
    (re.compile(r"while\s+True\s*:"), "R005_INFINITE_LOOP", "medium"),
    (re.compile(r"while\s+1\s*:"), "R005_INFINITE_LOOP", "medium"),
    (re.compile(r"time\.sleep\s*\(\s*(\d+)"), "R005_LONG_RUNNING_SLEEP", "medium"),
    (re.compile(r"open\s*\([^)]*['\"][wa]"), "R005_LARGE_FILE_WRITE", "medium"),
]

# ---------------------------------------------------------------------------
# Bash-specific patterns
# ---------------------------------------------------------------------------

BASH_DANGEROUS_DELETE_PATTERNS = [
    (re.compile(r"rm\s+-rf?\s"), "R001_BASH_RECURSIVE_DELETE", "critical"),
    (re.compile(r"find\s+.*-delete\b"), "R001_FILE_DANGEROUS_DELETE", "high"),
    (re.compile(r"xargs\s+rm\b"), "R001_FILE_DANGEROUS_DELETE", "high"),
]

BASH_NETWORK_PATTERNS = [
    (re.compile(r"\bcurl\b"), "R002_CURL_EXTERNAL_REQUEST", "medium"),
    (re.compile(r"\bwget\b"), "R002_WGET_EXTERNAL_REQUEST", "medium"),
    (re.compile(r"\bnc\b"), "R002_SOCKET_EXTERNAL_CONNECTION", "medium"),
    (re.compile(r"\bnetcat\b"), "R002_SOCKET_EXTERNAL_CONNECTION", "medium"),
    (re.compile(r"\bsocat\b"), "R002_SOCKET_EXTERNAL_CONNECTION", "medium"),
]

BASH_SYSTEM_PATTERNS = [
    (re.compile(r"\b(sudo|su)\b"), "R003_PRIVILEGE_ESCALATION_COMMAND", "high"),
    (re.compile(r"bash\s+-c\s"), "R003_SHELL_PIPE_EXECUTION", "high"),
    (re.compile(r"sh\s+-c\s"), "R003_SHELL_PIPE_EXECUTION", "high"),
    (re.compile(r"\beval\b"), "R003_SHELL_PIPE_EXECUTION", "medium"),
    (re.compile(r"python\d*\s+-c\s"), "R003_SHELL_PIPE_EXECUTION", "medium"),
    (re.compile(r"\bchmod\b"), "R003_PRIVILEGE_ESCALATION_COMMAND", "medium"),
    (re.compile(r"\bchown\b"), "R003_PRIVILEGE_ESCALATION_COMMAND", "medium"),
    (re.compile(r"&\s*$|&\s*;"), "R003_BACKGROUND_PROCESS_EXECUTION", "medium"),
]

BASH_RESOURCE_PATTERNS = [
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\}\s*;"), "R005_FORK_BOMB", "critical"),
    (re.compile(r"while\s+true"), "R005_INFINITE_LOOP", "medium"),
    (re.compile(r"\buntil\b"), "R005_INFINITE_LOOP", "medium"),
    (re.compile(r"sleep\s+(\d+)"), "R005_LONG_RUNNING_SLEEP", "medium"),
    (re.compile(r"xargs\s+-P\s*(\d+)"), "R005_EXCESSIVE_CONCURRENCY", "medium"),
    (re.compile(r"parallel\s+-j\s*(\d+)"), "R005_EXCESSIVE_CONCURRENCY", "medium"),
    (re.compile(r"head\s+-c\s*(\d+)"), "R005_LARGE_FILE_WRITE", "medium"),
]

BASH_SECRET_PATTERNS = [
    (
        re.compile(r"echo\s+\$?\w*(token|key|pass|secret|credential)\w*", re.IGNORECASE),
        "R006_SECRET_OUTPUT",
        "medium",
    ),
    (
        re.compile(r"curl\s+.*\$\w*(token|key|pass|secret)\w*", re.IGNORECASE),
        "R006_SECRET_NETWORK_TRANSMISSION",
        "high",
    ),
]


def sanitize_text(text: str, extra_patterns: list[str] | None = None) -> str:
    """Replace secret patterns in text with [SANITIZED].

    Args:
        text: The text to sanitize.
        extra_patterns: Additional regex patterns from PolicyConfig.secret_patterns.
    """
    text = SECRET_VALUE_RE.sub("[SANITIZED]", text)
    text = SECRET_KEY_VALUE_RE.sub(r"\1=[SANITIZED]", text)
    if extra_patterns:
        for pat in extra_patterns:
            try:
                text = re.sub(pat, "[SANITIZED]", text)
            except re.error:
                pass
    return text
