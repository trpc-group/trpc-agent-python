# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Extract scanner requests from common tool argument shapes."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from collections.abc import Sequence
from pathlib import Path
import re
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import TypeAdapter

from ._models import SafetyScanRequest
from ._models import ScriptLanguage

_PYTHON_NAMES = {"py", "python", "python3", "tool_code"}
_BASH_NAMES = {"bash", "command", "shell", "sh", "zsh"}
_PYTHON_OPTION_VALUES = {"--check-hash-based-pycs", "-W", "-X"}
_SHELL_OPTION_VALUES = {"--init-file", "--rcfile", "+O", "-O"}
_PYTHON_EXECUTABLE_RE = re.compile(r"python(?:\d+(?:\.\d+)*)?$")
_SHELL_ASSIGNMENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_SHELL_REDIRECTION_RE = re.compile(r"^(?:\d+|&)?(?:<>|>>?|<<-?|>&|<&)(.*)$")
_SHELL_CONTROL_TOKENS = {"&", "&&", ";", "|", "||"}
_PYTHON_PREFIX_FLAGS = frozenset("bBdEhiIOPqRsSuvVx")
_BOOL_ADAPTER = TypeAdapter(bool)


def normalize_script_language(value: Any, *, default: ScriptLanguage) -> ScriptLanguage:
    """Normalize common language aliases to one scanner language."""

    if isinstance(value, ScriptLanguage):
        return value
    if value is None or not str(value).strip():
        return default
    normalized = str(value).strip().lower()
    if normalized in _PYTHON_NAMES:
        return ScriptLanguage.PYTHON
    if normalized in _BASH_NAMES:
        return ScriptLanguage.BASH
    raise ValueError(f"unsupported script language: {value!r}")


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump()
    raise TypeError(f"tool safety arguments must be a mapping, got {type(value).__name__}")


def _first_present(data: Mapping[str, Any], names: Sequence[str]) -> tuple[Optional[str], Any]:
    for name in names:
        if name in data and data[name] is not None:
            return name, data[name]
    return None, None


def _script_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return shlex.join(str(item) for item in value)
    return str(value)


def _argv(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            return shlex.split(value)
        except ValueError:
            return [value]
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [str(item) for item in value]
    return [str(value)]


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    return int(value)


def _environment_keys(environment: Any) -> list[str]:
    if isinstance(environment, Mapping):
        return sorted(str(key) for key in environment)
    if isinstance(environment, Sequence) and not isinstance(environment, (str, bytes, bytearray)):
        return sorted(str(item).split("=", 1)[0] for item in environment)
    return []


def _python_cluster_value(token: str, option: str) -> Optional[str]:
    if not token.startswith("-") or token.startswith("--"):
        return None
    body = token[1:]
    option_index = body.find(option)
    if option_index < 0 or any(flag not in _PYTHON_PREFIX_FLAGS for flag in body[:option_index]):
        return None
    return body[option_index + 1:]


def _python_reads_stdin(command_argv: Sequence[str]) -> bool:
    """Return whether a direct Python invocation treats stdin as source."""

    interactive = False
    index = 1
    while index < len(command_argv):
        token = command_argv[index]
        if token == "-":
            return True
        if token == "--":
            index += 1
            return interactive or index >= len(command_argv) or command_argv[index] == "-"
        if token == "-i":
            interactive = True
            index += 1
            continue
        if _python_cluster_value(token, "c") is not None or _python_cluster_value(token, "m") is not None:
            return interactive
        if token in _PYTHON_OPTION_VALUES:
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return interactive
    return True


def _shell_reads_stdin(command_argv: Sequence[str]) -> bool:
    """Return whether a direct shell invocation treats stdin as source."""

    reads_stdin = False
    index = 1
    while index < len(command_argv):
        token = command_argv[index]
        if token == "--":
            index += 1
            return reads_stdin or index >= len(command_argv)
        if token in _SHELL_OPTION_VALUES:
            index += 2
            continue
        if token.startswith("--"):
            index += 1
            continue
        if token.startswith("-") and not token.startswith("--"):
            option_letters = token[1:]
            reads_stdin = reads_stdin or "i" in option_letters or "s" in option_letters
            if "c" in option_letters:
                return reads_stdin
            index += 1
            continue
        if token.startswith("+"):
            index += 1
            continue
        return reads_stdin
    return True


def _direct_command_argv(command: str) -> list[str]:
    """Return direct argv without shell assignments or redirections."""

    command_argv = shlex.split(command)
    normalized = []
    index = 0
    while index < len(command_argv):
        token = command_argv[index]
        if token in _SHELL_CONTROL_TOKENS:
            break
        redirection = _SHELL_REDIRECTION_RE.fullmatch(token)
        if redirection is not None:
            index += 1 if redirection.group(1) else 2
            continue
        if not normalized and _SHELL_ASSIGNMENT_RE.fullmatch(token):
            index += 1
            continue
        normalized.append(token)
        index += 1
    return normalized


def _unwrap_command_argv(command_argv: Sequence[str]) -> list[str]:
    """Remove common exec wrappers while retaining the effective command."""

    tokens = list(command_argv)
    for _ in range(6):
        if not tokens:
            break
        executable = Path(tokens[0]).name.lower()
        index = 1
        if executable == "env":
            while index < len(tokens):
                token = tokens[index]
                if token == "--":
                    index += 1
                    break
                if _SHELL_ASSIGNMENT_RE.fullmatch(token):
                    index += 1
                    continue
                if token in {"-C", "-S", "-u", "--chdir", "--split-string", "--unset"}:
                    index += 2
                    continue
                if token.startswith("-"):
                    index += 1
                    continue
                break
        elif executable in {"builtin", "command", "nohup", "time"}:
            if executable == "command" and any(token in {"-V", "-v"} for token in tokens[1:]):
                return []
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
        elif executable == "nice":
            while index < len(tokens):
                token = tokens[index]
                if token in {"-n", "--adjustment"}:
                    index += 2
                elif token.startswith("-"):
                    index += 1
                else:
                    break
        elif executable == "timeout":
            while index < len(tokens):
                token = tokens[index]
                if token in {"-k", "-s", "--kill-after", "--signal"}:
                    index += 2
                elif token.startswith("-"):
                    index += 1
                else:
                    index += 1  # duration
                    break
        else:
            break
        tokens = tokens[index:]
    return tokens


def _effective_command_argv(command: str, extra_argv: Sequence[str] = ()) -> list[str]:
    return _unwrap_command_argv([*_direct_command_argv(command), *extra_argv])


def _inline_interpreter_payloads(command_argv: Sequence[str]) -> list[tuple[str, ScriptLanguage]]:
    if not command_argv:
        return []
    executable = Path(command_argv[0]).name.lower()
    if executable in _PYTHON_NAMES or _PYTHON_EXECUTABLE_RE.fullmatch(executable):
        index = 1
        while index < len(command_argv):
            token = command_argv[index]
            inline_code = _python_cluster_value(token, "c")
            if inline_code is not None:
                if inline_code:
                    return [(inline_code, ScriptLanguage.PYTHON)]
                return ([(command_argv[index + 1], ScriptLanguage.PYTHON)] if index + 1 < len(command_argv) else [])
            if _python_cluster_value(token, "m") is not None or token == "--" or not token.startswith("-"):
                return []
            index += 2 if token in _PYTHON_OPTION_VALUES else 1
        return []
    if executable in _BASH_NAMES:
        for index, token in enumerate(command_argv[1:], start=1):
            if token == "-c" or (token.startswith("-") and "c" in token[1:]):
                return ([(command_argv[index + 1], ScriptLanguage.BASH)] if index + 1 < len(command_argv) else [])
            if not token.startswith(("-", "+")):
                return []
    return []


def _stdin_language(command: str, extra_argv: Sequence[str] = ()) -> Optional[ScriptLanguage]:
    try:
        command_argv = _effective_command_argv(command, extra_argv)
    except ValueError:
        return ScriptLanguage.BASH
    if not command_argv:
        return None
    executable = Path(command_argv[0]).name.lower()
    if (executable in _PYTHON_NAMES
            or _PYTHON_EXECUTABLE_RE.fullmatch(executable)) and _python_reads_stdin(command_argv):
        return ScriptLanguage.PYTHON
    if executable in _BASH_NAMES and _shell_reads_stdin(command_argv):
        return ScriptLanguage.BASH
    return None


def extract_safety_requests(args: Any, *, tool_name: str = "unknown_tool") -> list[SafetyScanRequest]:
    """Extract every executable payload from common tool argument shapes.

    Environment values are never copied into the returned model. Only their
    names are retained for rules that reason about sensitive variables.
    """

    data = _as_mapping(args)
    _, language_value = _first_present(data, ("language", "lang"))
    _, cwd = _first_present(data, ("cwd", "working_dir"))
    _, environment = _first_present(data, ("env", "environment"))
    _, argv = _first_present(data, ("argv", "args"))
    argv_values = _argv(argv)
    _, timeout = _first_present(data, ("timeout_seconds", "timeout_sec", "timeout"))
    _, output_limit = _first_present(data, ("output_limit_bytes", "max_output"))
    common = {
        "tool_name": tool_name,
        "argv": argv_values,
        "cwd": str(cwd) if cwd is not None else None,
        "environment_keys": _environment_keys(environment),
        "timeout_seconds": _optional_float(timeout),
        "output_limit_bytes": _optional_int(output_limit),
    }

    payloads: list[tuple[str, str, ScriptLanguage]] = []
    present_fields: list[tuple[str, Any, ScriptLanguage]] = []
    python_language = normalize_script_language(language_value, default=ScriptLanguage.PYTHON)
    for field_name in ("code", "script", "source"):
        if field_name not in data or data[field_name] is None:
            continue
        value = _script_text(data[field_name])
        present_fields.append((field_name, value, python_language))
        if value.strip():
            payloads.append((field_name, value, python_language))

    command_field, command_value = _first_present(data, ("command", "cmd"))
    command = _script_text(command_value) if command_field is not None else ""
    if command_field is not None:
        present_fields.append((command_field, command, ScriptLanguage.BASH))
        if command.strip():
            payloads.append((command_field, command, ScriptLanguage.BASH))
        try:
            effective_command_argv = _effective_command_argv(command, argv_values)
        except ValueError:
            effective_command_argv = []
        if effective_command_argv and argv_values:
            payloads.append((f"{command_field}_argv", shlex.join(effective_command_argv), ScriptLanguage.BASH))
        for inline_script, inline_language in _inline_interpreter_payloads(effective_command_argv):
            if inline_script.strip():
                payloads.append((f"{command_field}_inline", inline_script, inline_language))

    stdin_field, stdin_value = _first_present(data, ("stdin", "chars"))
    if stdin_field is not None:
        stdin_script = _script_text(stdin_value)
        stdin_language = _stdin_language(command, argv_values)
        if stdin_language is None and (stdin_field == "chars" or "write_stdin" in tool_name.lower()):
            stdin_language = ScriptLanguage.BASH
        if stdin_language is not None:
            present_fields.append((stdin_field, stdin_script, stdin_language))
            if stdin_script.strip():
                payloads.append((stdin_field, stdin_script, stdin_language))

    if not payloads and present_fields:
        payloads.append(present_fields[0])

    requests = []
    seen: set[tuple[str, ScriptLanguage]] = set()
    background = _BOOL_ADAPTER.validate_python(data.get("background", False))
    for source_field, script, language in payloads:
        dedupe_key = (script, language)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        requests.append(
            SafetyScanRequest(
                script=script,
                language=language,
                metadata={
                    "source_field": source_field,
                    "background": background
                },
                **common,
            ))
    return requests


def extract_safety_request(args: Any, *, tool_name: str = "unknown_tool") -> Optional[SafetyScanRequest]:
    """Extract one payload for callers that explicitly require a single script."""

    requests = extract_safety_requests(args, tool_name=tool_name)
    if len(requests) > 1:
        raise ValueError("multiple executable payloads found; use extract_safety_requests")
    return requests[0] if requests else None
