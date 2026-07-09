"""Base rule abstraction and rule registry for Script Safety Guard.

Design:
- BaseRule is the ABC that all safety rules must implement.
- RuleRegistry is a singleton that holds all registered rule instances.
- @register_rule is a class decorator that instantiates and registers a rule.

Usage:
    @register_rule
    class MyRule(BaseRule):
        rule_id = "CUSTOM-001"
        category = RiskCategory.NETWORK
        severity = Severity.HIGH
        languages = [Language.PYTHON, Language.BASH]
        description = "Detects ..."

        def scan(self, ctx: ScanContext) -> list[Finding]:
            ...
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from trpc_agent_sdk.tools.safety.models import (
    Finding,
    Language,
    RiskCategory,
    Severity,
)

if TYPE_CHECKING:
    from trpc_agent_sdk.tools.safety.models import ScanContext

logger = logging.getLogger(__name__)


class BaseRule(ABC):
    """Abstract base class for all safety detection rules.

    Subclasses must define class-level attributes and implement scan().
    """

    # --- Required class attributes (must be set by subclass) ---
    rule_id: str = ""
    """Unique rule identifier, e.g. 'FS-001', 'NET-002'."""

    category: RiskCategory = RiskCategory.PROCESS
    """Risk category this rule belongs to."""

    severity: Severity = Severity.MEDIUM
    """Default severity when this rule triggers."""

    languages: list[Language] = []
    """Languages this rule applies to. Empty = all languages."""

    description: str = ""
    """Human-readable description of what this rule detects."""

    @abstractmethod
    def scan(self, ctx: "ScanContext") -> list[Finding]:
        """Scan the given context and return any findings.

        Args:
            ctx: ScanContext containing source code, AST, language, etc.

        Returns:
            List of Finding objects. Empty list means no risk detected.
        """
        ...

    def supports_language(self, language: Language) -> bool:
        """Check if this rule applies to the given language."""
        if not self.languages:
            return True  # Empty = applies to all
        return language in self.languages

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} rule_id={self.rule_id!r} severity={self.severity.value}>"


class RuleRegistry:
    """Singleton registry for all safety rules.

    Rules are registered via @register_rule decorator or manual register() call.
    The Guard engine queries this registry to get applicable rules for scanning.
    """

    _instance: RuleRegistry | None = None
    _rules: dict[str, BaseRule]

    def __new__(cls) -> RuleRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._rules = {}
        return cls._instance

    def register(self, rule: BaseRule) -> None:
        """Register a rule instance. Duplicate rule_id will overwrite with warning."""
        if not rule.rule_id:
            raise ValueError(f"Rule {rule.__class__.__name__} has empty rule_id.")
        if rule.rule_id in self._rules:
            logger.warning(
                "Rule '%s' already registered (class=%s), overwriting with %s.",
                rule.rule_id,
                self._rules[rule.rule_id].__class__.__name__,
                rule.__class__.__name__,
            )
        self._rules[rule.rule_id] = rule
        logger.debug("Registered rule: %s (%s)", rule.rule_id, rule.__class__.__name__)

    def unregister(self, rule_id: str) -> None:
        """Remove a rule by ID. No-op if not found."""
        self._rules.pop(rule_id, None)

    def get_all(self) -> list[BaseRule]:
        """Return all registered rules."""
        return list(self._rules.values())

    def get_by_language(self, language: Language) -> list[BaseRule]:
        """Return rules applicable to the given language."""
        return [r for r in self._rules.values() if r.supports_language(language)]

    def get_by_category(self, category: RiskCategory) -> list[BaseRule]:
        """Return rules belonging to the given category."""
        return [r for r in self._rules.values() if r.category == category]

    def get_by_id(self, rule_id: str) -> BaseRule | None:
        """Return a specific rule by ID, or None."""
        return self._rules.get(rule_id)

    def clear(self) -> None:
        """Remove all registered rules. Primarily for testing."""
        self._rules.clear()

    @property
    def count(self) -> int:
        """Number of registered rules."""
        return len(self._rules)

    def __contains__(self, rule_id: str) -> bool:
        return rule_id in self._rules

    def __repr__(self) -> str:
        return f"<RuleRegistry rules={self.count}>"


# Module-level singleton instance
rule_registry = RuleRegistry()


def register_rule(cls: type[BaseRule]) -> type[BaseRule]:
    """Class decorator that instantiates a BaseRule subclass and registers it.

    Usage:
        @register_rule
        class MyRule(BaseRule):
            rule_id = "MY-001"
            ...
    """
    if not issubclass(cls, BaseRule):
        raise TypeError(f"@register_rule can only decorate BaseRule subclasses, got {cls}")
    instance = cls()
    rule_registry.register(instance)
    return cls
