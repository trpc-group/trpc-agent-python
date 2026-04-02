# -*- coding: utf-8 -*-
"""Unit tests for SessionConfig."""

from trpc_agent_sdk.server.agents.claude._session_config import SessionConfig


class TestSessionConfig:
    def test_default_ttl(self):
        config = SessionConfig()
        assert config.ttl == 600

    def test_custom_ttl(self):
        config = SessionConfig(ttl=1200)
        assert config.ttl == 1200

    def test_zero_ttl_disables_cleanup(self):
        config = SessionConfig(ttl=0)
        assert config.ttl == 0

    def test_negative_ttl_disables_cleanup(self):
        config = SessionConfig(ttl=-1)
        assert config.ttl == -1

    def test_is_dataclass(self):
        from dataclasses import fields
        field_names = [f.name for f in fields(SessionConfig)]
        assert "ttl" in field_names

    def test_equality(self):
        c1 = SessionConfig(ttl=300)
        c2 = SessionConfig(ttl=300)
        assert c1 == c2

    def test_inequality(self):
        c1 = SessionConfig(ttl=300)
        c2 = SessionConfig(ttl=600)
        assert c1 != c2
