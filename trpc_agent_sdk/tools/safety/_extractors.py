# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Extract ScanRequest from different tool types for safety scanning."""

from __future__ import annotations

from typing import Any
from typing import Dict

from ._types import ScanRequest
from ._types import ScanTarget
from ._types import ScriptLanguage
from ._types import normalize_language


def extract_tool_safety_context(tool: Any,
                                args: Dict[str, Any],
                                target: ScanTarget = ScanTarget.TOOL) -> ScanRequest | None:
    """Extract a ScanRequest from a tool and its arguments.

    Returns None when no executable script content can be identified
    (e.g. pure business parameters like {"city": "Tokyo"}).
    """
    tool_name = getattr(tool, 'name', '') or ''
    tool_name_str = str(tool_name)

    # BashTool path
    if 'command' in args and isinstance(args['command'], str):
        return _extract_from_bash(tool_name_str, args, target)

    # Script path
    if 'script' in args and isinstance(args['script'], str):
        return _extract_from_script(args, target)

    # Code path
    if 'code' in args and isinstance(args['code'], str):
        return _extract_from_code(args, target)

    # Generic: search for any executable-content key
    for key in ('command', 'shell_command', 'cmd', 'source', 'content'):
        if key in args and isinstance(args[key], str) and len(args[key]) > 5:
            return _extract_generic(tool_name_str, args, key, target)

    return None


def _resolve_language(args: Dict[str, Any], default: ScriptLanguage = ScriptLanguage.BASH) -> ScriptLanguage:
    """Resolve language from args['language'] or default."""
    lang_raw = args.get('language', '')
    if lang_raw:
        return normalize_language(str(lang_raw))
    return default


def _extract_from_bash(tool_name: str, args: Dict[str, Any], target: ScanTarget) -> ScanRequest:
    return ScanRequest(
        script=args['command'],
        language=_resolve_language(args, ScriptLanguage.BASH),
        tool_name=tool_name,
        target=target,
        cwd=str(args.get('cwd', '')),
        env=args.get('env', {}),
        tool_metadata={'timeout': args.get('timeout', 0)},
    )


def _extract_from_script(args: Dict[str, Any], target: ScanTarget) -> ScanRequest:
    lang = _resolve_language(args, ScriptLanguage.PYTHON)
    return ScanRequest(
        script=args['script'],
        language=lang,
        tool_name=args.get('tool_name', 'unknown'),
        target=target,
        args=args.get('args', []),
        cwd=str(args.get('cwd', '')),
        env=args.get('env', {}),
        tool_metadata=args.get('metadata', {}),
    )


def _extract_from_code(args: Dict[str, Any], target: ScanTarget) -> ScanRequest:
    lang = _resolve_language(args, ScriptLanguage.PYTHON)
    return ScanRequest(
        script=args['code'],
        language=lang,
        tool_name=args.get('tool_name', 'unknown'),
        target=target,
        tool_metadata=args.get('metadata', {}),
    )


def _extract_generic(tool_name: str, args: Dict[str, Any], key: str, target: ScanTarget) -> ScanRequest:
    return ScanRequest(
        script=args[key],
        language=ScriptLanguage.BASH,
        tool_name=tool_name,
        target=target,
        tool_metadata=args.get('metadata', {}),
    )
