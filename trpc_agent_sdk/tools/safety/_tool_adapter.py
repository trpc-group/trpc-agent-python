"""Build SafetyScanRequest from tool invocation args.

The adapter reads the declarative field mapping from the policy so
custom tools can be supported without code changes. Built-in adapters
cover ``workspace_exec``, ``skill_run`` and ``skill_exec``; MCP and
custom tools use the same declarative form.
"""

from __future__ import annotations

from typing import Any, Mapping

from trpc_agent_sdk.tools.safety._exceptions import ToolRequestError
from trpc_agent_sdk.tools.safety._models import SafetyScanRequest, ScriptLanguage, ToolKind
from trpc_agent_sdk.tools.safety._policy import ToolFieldMapping, ToolSafetyPolicy

# Default mappings keyed by the canonical tool name. The keys must match
# what the framework passes as ``tool_name`` to the filter; they can be
# overridden or extended through ``policy.tools``.
_BUILTIN_DEFAULTS: dict[str, ToolFieldMapping] = {
    "workspace_exec":
    ToolFieldMapping(
        execution_capable=True,
        language=ScriptLanguage.BASH,
        script="command",
        cwd="cwd",
        env="env",
        timeout="timeout_sec",
    ),
    "skill_run":
    ToolFieldMapping(
        execution_capable=True,
        language=ScriptLanguage.BASH,
        script="command",
        cwd="cwd",
        env="env",
        timeout="timeout",
    ),
    "skill_exec":
    ToolFieldMapping(
        execution_capable=True,
        language=ScriptLanguage.BASH,
        script="command",
        cwd="cwd",
        env="env",
        timeout="timeout",
    ),
    "python_exec":
    ToolFieldMapping(
        execution_capable=True,
        language=ScriptLanguage.PYTHON,
        script="code",
        cwd="cwd",
        env="env",
        timeout="timeout",
    ),
    "bash_exec":
    ToolFieldMapping(
        execution_capable=True,
        language=ScriptLanguage.BASH,
        script="command",
        cwd="cwd",
        env="env",
        timeout="timeout",
    ),
}


class ToolInputAdapter:
    """Translate a tool's invocation args into a SafetyScanRequest.

    The adapter is intentionally pure: it does not call the tool, read
    files, or evaluate the script. It only normalizes the declared
    arguments into the request shape the guard expects.
    """

    def __init__(
        self,
        tool_name: str,
        mapping: ToolFieldMapping,
        *,
        tool_kind: ToolKind = ToolKind.UNKNOWN,
    ) -> None:
        self.tool_name = tool_name
        self.mapping = mapping
        self.tool_kind = tool_kind

    def build_request(
        self,
        args: Mapping[str, Any],
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> SafetyScanRequest:
        script = _extract_scalar(args, self.mapping.script, required=self.mapping.execution_capable)
        if script is None and self.mapping.execution_capable:
            raise ToolRequestError(f"tool {self.tool_name!r} is execution-capable but no "
                                   f"script field was found (expected field "
                                   f"{self.mapping.script!r})")
        cwd = _extract_scalar(args, self.mapping.cwd, required=False)
        env = _extract_mapping(args, self.mapping.env)
        argv_value = _extract_sequence(args, self.mapping.argv)
        timeout = _extract_float(args, self.mapping.timeout)
        merged_metadata: dict[str, Any] = {
            "execution_capable": self.mapping.execution_capable,
            "adapter_id": self.tool_name,
        }
        if metadata:
            merged_metadata.update(metadata)
        return SafetyScanRequest(
            tool_name=self.tool_name,
            tool_kind=self.tool_kind,
            language=self.mapping.language,
            script=script or "",
            argv=argv_value,
            cwd=cwd,
            env=env,
            metadata=merged_metadata,
            requested_timeout_seconds=timeout,
        )


def build_default_adapters(policy: ToolSafetyPolicy, ) -> dict[str, ToolInputAdapter]:
    """Build adapters for builtin tools, allowing policy overrides."""

    out: dict[str, ToolInputAdapter] = {}
    for name, default in _BUILTIN_DEFAULTS.items():
        mapping = policy.tools.get(name, default)
        out[name] = ToolInputAdapter(name, mapping)
    for name, mapping in policy.tools.items():
        if name in out:
            continue
        out[name] = ToolInputAdapter(name, mapping)
    return out


def resolve_adapter(
    tool_name: str,
    policy: ToolSafetyPolicy,
    *,
    builtin: dict[str, ToolInputAdapter] | None = None,
) -> ToolInputAdapter:
    """Pick the adapter for ``tool_name``.

    Built-ins win first; otherwise the policy-declared mapping is used.
    A tool with no mapping returns an adapter whose language is UNKNOWN
    so the guard falls back to ``needs_human_review``.
    """

    if builtin and tool_name in builtin:
        return builtin[tool_name]
    mapping = policy.tools.get(tool_name) or ToolFieldMapping(
        execution_capable=False,
        language=ScriptLanguage.UNKNOWN,
    )
    return ToolInputAdapter(tool_name, mapping)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _extract_scalar(
    args: Mapping[str, Any],
    field_name: str | None,
    *,
    required: bool,
) -> str | None:
    if not field_name:
        return None
    if field_name not in args:
        if required:
            raise ToolRequestError(f"required field {field_name!r} missing from tool args")
        return None
    value = args[field_name]
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return str(value)


def _extract_mapping(
    args: Mapping[str, Any],
    field_name: str | None,
) -> dict[str, str]:
    if not field_name or field_name not in args:
        return {}
    value = args[field_name]
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ToolRequestError(f"field {field_name!r} must be a mapping; got {type(value)!r}")
    return {str(k): str(v) for k, v in value.items()}


def _extract_sequence(
    args: Mapping[str, Any],
    field_name: str | None,
) -> tuple[str, ...]:
    if not field_name or field_name not in args:
        return ()
    value = args[field_name]
    if value is None:
        return ()
    if isinstance(value, str):
        return (value, )
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return (str(value), )


def _extract_float(
    args: Mapping[str, Any],
    field_name: str | None,
) -> float | None:
    if not field_name or field_name not in args:
        return None
    value = args[field_name]
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
