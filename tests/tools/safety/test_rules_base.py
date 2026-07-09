"""Unit tests for BaseRule, RuleRegistry, and @register_rule decorator."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety.models import (
    Decision,
    Finding,
    Language,
    RiskCategory,
    ScanContext,
    Severity,
    ToolMetadata,
)
from trpc_agent_sdk.tools.safety.rules._base import (
    BaseRule,
    RuleRegistry,
    register_rule,
    rule_registry,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_context(source: str = "", language: Language = Language.PYTHON) -> ScanContext:
    """Helper to create a minimal ScanContext for testing."""
    return ScanContext(
        source_code=source,
        language=language,
        lines=source.splitlines(),
    )


def _make_finding(rule_id: str = "TEST-001") -> Finding:
    """Helper to create a Finding."""
    return Finding(
        rule_id=rule_id,
        category=RiskCategory.PROCESS,
        severity=Severity.HIGH,
        decision=Decision.DENY,
        description="Test finding",
    )


# ---------------------------------------------------------------------------
# Concrete test rules (not registered globally)
# ---------------------------------------------------------------------------


class DummyRule(BaseRule):
    """A concrete rule for testing that always returns one finding."""

    rule_id = "DUMMY-001"
    category = RiskCategory.PROCESS
    severity = Severity.HIGH
    languages = [Language.PYTHON]
    description = "Dummy rule for testing"

    def scan(self, ctx: ScanContext) -> list[Finding]:
        return [_make_finding(self.rule_id)]


class DummyBashRule(BaseRule):
    """A bash-only rule for testing."""

    rule_id = "DUMMY-002"
    category = RiskCategory.NETWORK
    severity = Severity.MEDIUM
    languages = [Language.BASH]
    description = "Dummy bash rule"

    def scan(self, ctx: ScanContext) -> list[Finding]:
        return []


class DummyAllLangRule(BaseRule):
    """A rule that applies to all languages (empty languages list)."""

    rule_id = "DUMMY-003"
    category = RiskCategory.FILE_OPERATIONS
    severity = Severity.LOW
    languages = []  # All languages
    description = "Applies to all languages"

    def scan(self, ctx: ScanContext) -> list[Finding]:
        return []


# ---------------------------------------------------------------------------
# Tests: BaseRule
# ---------------------------------------------------------------------------


class TestBaseRule:
    """Test BaseRule ABC behavior."""

    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseRule()  # type: ignore

    def test_concrete_rule_instantiates(self):
        rule = DummyRule()
        assert rule.rule_id == "DUMMY-001"
        assert rule.category == RiskCategory.PROCESS
        assert rule.severity == Severity.HIGH

    def test_supports_language_specific(self):
        rule = DummyRule()
        assert rule.supports_language(Language.PYTHON) is True
        assert rule.supports_language(Language.BASH) is False

    def test_supports_language_all(self):
        rule = DummyAllLangRule()
        assert rule.supports_language(Language.PYTHON) is True
        assert rule.supports_language(Language.BASH) is True

    def test_scan_returns_findings(self):
        rule = DummyRule()
        ctx = _make_context("import os")
        findings = rule.scan(ctx)
        assert len(findings) == 1
        assert findings[0].rule_id == "DUMMY-001"

    def test_scan_returns_empty(self):
        rule = DummyBashRule()
        ctx = _make_context("echo hello", Language.BASH)
        findings = rule.scan(ctx)
        assert findings == []

    def test_repr(self):
        rule = DummyRule()
        repr_str = repr(rule)
        assert "DUMMY-001" in repr_str
        assert "high" in repr_str


# ---------------------------------------------------------------------------
# Tests: RuleRegistry
# ---------------------------------------------------------------------------


class TestRuleRegistry:
    """Test RuleRegistry singleton and operations."""

    def setup_method(self):
        """Clear the global registry before each test."""
        rule_registry.clear()

    def teardown_method(self):
        """Clear after each test to avoid pollution."""
        rule_registry.clear()

    def test_singleton(self):
        r1 = RuleRegistry()
        r2 = RuleRegistry()
        assert r1 is r2

    def test_register_and_get_all(self):
        rule = DummyRule()
        rule_registry.register(rule)
        assert rule_registry.count == 1
        assert rule_registry.get_all() == [rule]

    def test_register_multiple(self):
        rule_registry.register(DummyRule())
        rule_registry.register(DummyBashRule())
        rule_registry.register(DummyAllLangRule())
        assert rule_registry.count == 3

    def test_duplicate_rule_id_overwrites(self):
        rule1 = DummyRule()
        rule2 = DummyRule()  # Same rule_id
        rule_registry.register(rule1)
        rule_registry.register(rule2)
        assert rule_registry.count == 1
        assert rule_registry.get_by_id("DUMMY-001") is rule2

    def test_register_empty_rule_id_raises(self):

        class BadRule(BaseRule):
            rule_id = ""
            category = RiskCategory.PROCESS
            severity = Severity.LOW
            languages = []
            description = "bad"

            def scan(self, ctx: ScanContext) -> list[Finding]:
                return []

        with pytest.raises(ValueError, match="empty rule_id"):
            rule_registry.register(BadRule())

    def test_unregister(self):
        rule_registry.register(DummyRule())
        assert "DUMMY-001" in rule_registry
        rule_registry.unregister("DUMMY-001")
        assert "DUMMY-001" not in rule_registry
        assert rule_registry.count == 0

    def test_unregister_nonexistent_is_noop(self):
        rule_registry.unregister("NONEXIST-999")  # Should not raise

    def test_get_by_language_python(self):
        rule_registry.register(DummyRule())  # Python only
        rule_registry.register(DummyBashRule())  # Bash only
        rule_registry.register(DummyAllLangRule())  # All

        python_rules = rule_registry.get_by_language(Language.PYTHON)
        rule_ids = [r.rule_id for r in python_rules]
        assert "DUMMY-001" in rule_ids  # Python
        assert "DUMMY-002" not in rule_ids  # Bash only
        assert "DUMMY-003" in rule_ids  # All languages

    def test_get_by_language_bash(self):
        rule_registry.register(DummyRule())
        rule_registry.register(DummyBashRule())
        rule_registry.register(DummyAllLangRule())

        bash_rules = rule_registry.get_by_language(Language.BASH)
        rule_ids = [r.rule_id for r in bash_rules]
        assert "DUMMY-001" not in rule_ids  # Python only
        assert "DUMMY-002" in rule_ids  # Bash
        assert "DUMMY-003" in rule_ids  # All

    def test_get_by_category(self):
        rule_registry.register(DummyRule())  # PROCESS
        rule_registry.register(DummyBashRule())  # NETWORK
        rule_registry.register(DummyAllLangRule())  # FILE_OPERATIONS

        process_rules = rule_registry.get_by_category(RiskCategory.PROCESS)
        assert len(process_rules) == 1
        assert process_rules[0].rule_id == "DUMMY-001"

        network_rules = rule_registry.get_by_category(RiskCategory.NETWORK)
        assert len(network_rules) == 1
        assert network_rules[0].rule_id == "DUMMY-002"

    def test_get_by_id_found(self):
        rule_registry.register(DummyRule())
        result = rule_registry.get_by_id("DUMMY-001")
        assert result is not None
        assert result.rule_id == "DUMMY-001"

    def test_get_by_id_not_found(self):
        assert rule_registry.get_by_id("NONEXIST") is None

    def test_contains(self):
        rule_registry.register(DummyRule())
        assert "DUMMY-001" in rule_registry
        assert "NOPE" not in rule_registry

    def test_clear(self):
        rule_registry.register(DummyRule())
        rule_registry.register(DummyBashRule())
        rule_registry.clear()
        assert rule_registry.count == 0
        assert rule_registry.get_all() == []

    def test_repr(self):
        rule_registry.register(DummyRule())
        assert "rules=1" in repr(rule_registry)


# ---------------------------------------------------------------------------
# Tests: @register_rule decorator
# ---------------------------------------------------------------------------


class TestRegisterRuleDecorator:
    """Test the @register_rule class decorator."""

    def setup_method(self):
        rule_registry.clear()

    def teardown_method(self):
        rule_registry.clear()

    def test_decorator_registers_rule(self):

        @register_rule
        class AutoRegistered(BaseRule):
            rule_id = "AUTO-001"
            category = RiskCategory.SECRETS
            severity = Severity.HIGH
            languages = [Language.PYTHON]
            description = "Auto-registered"

            def scan(self, ctx: ScanContext) -> list[Finding]:
                return []

        assert "AUTO-001" in rule_registry
        assert rule_registry.get_by_id("AUTO-001") is not None

    def test_decorator_returns_class(self):

        @register_rule
        class MyRule(BaseRule):
            rule_id = "AUTO-002"
            category = RiskCategory.RESOURCE
            severity = Severity.MEDIUM
            languages = [Language.BASH]
            description = "Test"

            def scan(self, ctx: ScanContext) -> list[Finding]:
                return []

        # The decorator should return the class itself
        assert MyRule.rule_id == "AUTO-002"

    def test_decorator_rejects_non_baserule(self):
        with pytest.raises(TypeError, match="BaseRule subclasses"):

            @register_rule
            class NotARule:  # type: ignore
                rule_id = "BAD-001"

    def test_multiple_decorators(self):

        @register_rule
        class RuleA(BaseRule):
            rule_id = "MULTI-001"
            category = RiskCategory.NETWORK
            severity = Severity.LOW
            languages = []
            description = "A"

            def scan(self, ctx: ScanContext) -> list[Finding]:
                return []

        @register_rule
        class RuleB(BaseRule):
            rule_id = "MULTI-002"
            category = RiskCategory.DEPENDENCY
            severity = Severity.MEDIUM
            languages = [Language.PYTHON]
            description = "B"

            def scan(self, ctx: ScanContext) -> list[Finding]:
                return []

        assert rule_registry.count == 2
        assert "MULTI-001" in rule_registry
        assert "MULTI-002" in rule_registry
