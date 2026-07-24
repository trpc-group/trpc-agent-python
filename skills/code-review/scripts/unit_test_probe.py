"""Run a configured test command inside the sandbox."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys

SHELL_EXECUTABLES = {"sh", "bash", "zsh", "fish", "dash", "ksh"}


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
    executable = os.path.basename(argv[0])
    if executable in SHELL_EXECUTABLES:
        raise ValueError("shell interpreters are not allowed for sandbox test commands")
    return argv


if __name__ == "__main__":
    raise SystemExit(main())
