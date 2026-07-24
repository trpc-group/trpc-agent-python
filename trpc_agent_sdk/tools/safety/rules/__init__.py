"""Safety rules package.

Importing this package triggers registration of all built-in rules.
Rule modules are imported below to ensure @register_rule decorators execute.
"""

from trpc_agent_sdk.tools.safety.rules._base import (
    BaseRule,
    RuleRegistry,
    register_rule,
    rule_registry,
)

__all__ = [
    "BaseRule",
    "RuleRegistry",
    "register_rule",
    "rule_registry",
]

# --- Auto-import rule modules to trigger registration ---
from trpc_agent_sdk.tools.safety.rules import file_ops  # noqa: F401
from trpc_agent_sdk.tools.safety.rules import network  # noqa: F401
from trpc_agent_sdk.tools.safety.rules import process  # noqa: F401
from trpc_agent_sdk.tools.safety.rules import dependency  # noqa: F401
from trpc_agent_sdk.tools.safety.rules import resource  # noqa: F401
from trpc_agent_sdk.tools.safety.rules import secrets  # noqa: F401
