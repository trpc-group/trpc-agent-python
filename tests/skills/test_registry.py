# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import pytest
from trpc_agent_sdk.skills import SKILL_REGISTRY
from trpc_agent_sdk.skills import SkillRegistry


class TestSkillRegistry:
    """Test suite for SkillRegistry class."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        # Create a new instance for testing (not singleton)
        self.registry = SkillRegistry()
        self.registry.clear()

    def teardown_method(self):
        """Clean up after each test."""
        self.registry.clear()

    def test_register_skill(self):
        """Test registering a skill."""
        def test_skill():
            return "test"

        self.registry.register("test-skill", test_skill)

        assert self.registry.get("test-skill") == test_skill

    def test_register_duplicate_skill(self):
        """Test registering duplicate skill raises ValueError."""
        def test_skill():
            return "test"

        self.registry.register("test-skill", test_skill)

        with pytest.raises(ValueError, match="already registered"):
            self.registry.register("test-skill", test_skill)

    def test_unregister_skill(self):
        """Test unregistering a skill."""
        def test_skill():
            return "test"

        self.registry.register("test-skill", test_skill)
        self.registry.unregister("test-skill")

        assert self.registry.get("test-skill") is None

    def test_unregister_nonexistent_skill(self):
        """Test unregistering nonexistent skill does not raise error."""
        # Should not raise error
        self.registry.unregister("nonexistent-skill")

    def test_get_skill(self):
        """Test getting a skill by name."""
        def test_skill():
            return "test"

        self.registry.register("test-skill", test_skill)

        skill = self.registry.get("test-skill")

        assert skill == test_skill

    def test_get_nonexistent_skill(self):
        """Test getting nonexistent skill returns None."""
        skill = self.registry.get("nonexistent-skill")

        assert skill is None

    def test_get_all_skills(self):
        """Test getting all registered skills."""
        def skill1():
            return "skill1"

        def skill2():
            return "skill2"

        self.registry.register("skill1", skill1)
        self.registry.register("skill2", skill2)

        all_skills = self.registry.get_all()

        assert len(all_skills) == 2
        assert skill1 in all_skills
        assert skill2 in all_skills

    def test_get_all_skills_empty(self):
        """Test getting all skills when registry is empty."""
        all_skills = self.registry.get_all()

        assert len(all_skills) == 0

    def test_search_skills_by_name(self):
        """Test searching skills by name."""
        def python_skill():
            return "python"

        def bash_skill():
            return "bash"

        self.registry.register("python-tool", python_skill)
        self.registry.register("bash-script", bash_skill)

        results = self.registry.search("python")

        assert len(results) == 1
        assert python_skill in results

    def test_search_skills_case_insensitive(self):
        """Test searching skills is case insensitive."""
        def test_skill():
            return "test"

        self.registry.register("TestSkill", test_skill)

        results = self.registry.search("test")

        assert len(results) == 1
        assert test_skill in results

    def test_search_skills_no_match(self):
        """Test searching skills with no match returns empty list."""
        def test_skill():
            return "test"

        self.registry.register("test-skill", test_skill)

        results = self.registry.search("nonexistent")

        assert len(results) == 0

    def test_clear_registry(self):
        """Test clearing the registry."""
        def skill1():
            return "skill1"

        def skill2():
            return "skill2"

        self.registry.register("skill1", skill1)
        self.registry.register("skill2", skill2)

        self.registry.clear()

        assert len(self.registry.get_all()) == 0
        assert self.registry.get("skill1") is None
        assert self.registry.get("skill2") is None

    def test_multiple_registrations(self):
        """Test registering multiple skills."""
        def skill1():
            return "skill1"

        def skill2():
            return "skill2"

        def skill3():
            return "skill3"

        self.registry.register("skill1", skill1)
        self.registry.register("skill2", skill2)
        self.registry.register("skill3", skill3)

        assert len(self.registry.get_all()) == 3
        assert self.registry.get("skill1") == skill1
        assert self.registry.get("skill2") == skill2
        assert self.registry.get("skill3") == skill3


class TestSKILLREGISTRY:
    """Test suite for SKILL_REGISTRY singleton."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        SKILL_REGISTRY.clear()

    def teardown_method(self):
        """Clean up after each test."""
        SKILL_REGISTRY.clear()

    def test_skill_registry_singleton(self):
        """Test that SKILL_REGISTRY is a singleton."""
        registry1 = SKILL_REGISTRY
        registry2 = SKILL_REGISTRY

        assert registry1 is registry2
        assert isinstance(registry1, SkillRegistry)

    def test_skill_registry_operations(self):
        """Test operations on SKILL_REGISTRY singleton."""
        def test_skill():
            return "test"

        SKILL_REGISTRY.register("test-skill", test_skill)

        assert SKILL_REGISTRY.get("test-skill") == test_skill

        SKILL_REGISTRY.unregister("test-skill")

        assert SKILL_REGISTRY.get("test-skill") is None

