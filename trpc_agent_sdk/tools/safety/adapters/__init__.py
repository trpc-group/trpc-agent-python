"""Safety Guard adapter layer.

Provides two integration patterns:
- ScriptSafetyFilter: Tool filter adapter (intercepts tool execution via filter chain)
- SafeCodeExecutor: CodeExecutor wrapper adapter (wraps any BaseCodeExecutor)
"""

from trpc_agent_sdk.tools.safety.adapters.filter_adapter import ScriptSafetyFilter
from trpc_agent_sdk.tools.safety.adapters.wrapper_adapter import SafeCodeExecutor

__all__ = [
    "ScriptSafetyFilter",
    "SafeCodeExecutor",
]
