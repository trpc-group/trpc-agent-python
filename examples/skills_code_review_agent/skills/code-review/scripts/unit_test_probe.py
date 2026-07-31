"""Run a configured test command inside the sandbox."""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "dash", "ksh"}
SAFE_TEST_MODULES = {"pytest", "unittest"}
UNSAFE_PYTHON_OPTIONS = {"-c", "-W"}
UNSAFE_TEST_PATH_MARKERS = (
    ".env",
    ".ssh/",
    "id_rsa",
    "private_key",
    ".aws/",
    "/etc/",
    "secrets/",
)


def main() -> int:
    command = _normalize_python_command(os.environ.get("CR_TEST_COMMAND",
                                                       f"{shlex.quote(sys.executable)} -m pytest -q"))
    if os.environ.get("CR_ALLOW_TEST_COMMAND") != "1":
        print("unit test command skipped; set CR_ALLOW_TEST_COMMAND=1 to execute")
        return 0
    try:
        argv = _safe_command_argv(command)
    except ValueError as exc:
        sys.stderr.write(f"unit test command rejected: {exc}\n")
        return 126
    result = subprocess.run(
        argv,
        text=True,
        capture_output=True,
        timeout=float(os.environ.get("CR_TEST_TIMEOUT", "30")),
        cwd=os.environ.get("CR_REPO_PATH") or None,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def _normalize_python_command(command: str) -> str:
    if command == "python":
        return shlex.quote(sys.executable)
    if command.startswith("python "):
        return f"{shlex.quote(sys.executable)} {command.removeprefix('python ')}"
    return command


def _safe_command_argv(command: str) -> list[str]:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise ValueError(f"cannot parse command: {exc}") from exc
    if not argv:
        raise ValueError("empty command")
    executable = os.path.basename(argv[0]).lower()
    if executable in SHELL_EXECUTABLES:
        raise ValueError("shell interpreters are not allowed for sandbox test commands")
    if not _is_python_executable(argv[0]):
        raise ValueError("sandbox test commands must use a Python interpreter")
    for arg in argv[1:]:
        if any(arg == option or arg.startswith(option) for option in UNSAFE_PYTHON_OPTIONS):
            raise ValueError(f"Python option {arg!r} is not allowed for sandbox test commands")
    try:
        module_index = argv.index("-m")
    except ValueError as exc:
        raise ValueError("sandbox test commands must run an allowed Python module with -m") from exc
    if module_index + 1 >= len(argv):
        raise ValueError("missing Python module after -m")
    module = argv[module_index + 1]
    if module not in SAFE_TEST_MODULES:
        raise ValueError(f"Python module {module!r} is not allowed for sandbox test commands")
    for arg in argv[module_index + 2:]:
        if _is_unsafe_test_path_arg(arg):
            raise ValueError(f"test path argument {arg!r} is not allowed for sandbox test commands")
    return argv


def _is_python_executable(executable: str) -> bool:
    name = os.path.basename(executable).lower()
    if name in {"python", "python.exe"}:
        return True
    if re.fullmatch(r"python3(?:\.\d+)?(?:\.exe)?", name):
        return True
    with contextlib.suppress(OSError, RuntimeError):
        return Path(executable).resolve() == Path(sys.executable).resolve()
    return False


def _is_unsafe_test_path_arg(arg: str) -> bool:
    normalized = arg.replace("\\", "/")
    lowered = normalized.lower()
    if lowered.startswith(("http://", "https://")):
        return False
    candidates = [normalized]
    if "=" in normalized:
        candidates.append(normalized.split("=", 1)[1])
    for candidate in candidates:
        candidate_lowered = candidate.lower()
        if candidate_lowered.startswith(("http://", "https://")):
            continue
        if candidate.startswith("/") or re.match(r"^[A-Za-z]:/", candidate):
            return True
        if ".." in Path(candidate).parts:
            return True
        if any(marker in candidate_lowered for marker in UNSAFE_TEST_PATH_MARKERS):
            return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
