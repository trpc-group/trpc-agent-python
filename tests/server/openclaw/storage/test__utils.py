"""Tests for trpc_agent_sdk.server.openclaw.storage._utils."""

from unittest.mock import MagicMock

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.server.openclaw.storage._utils import (
    _agent_context,
    get_agent_context,
    get_memory_key,
    get_memory_key_from_save_key,
    get_memory_key_from_session,
    make_memory_key,
    set_agent_context,
)


class TestGetMemoryKey:
    """Tests for get_memory_key."""

    def test_basic(self):
        session = MagicMock()
        session.app_name = "myapp"
        session.user_id = "user42"
        session.id = "sess-001"
        assert get_memory_key(session) == "myapp/user42/sess-001"

    def test_empty_fields(self):
        session = MagicMock()
        session.app_name = ""
        session.user_id = ""
        session.id = ""
        assert get_memory_key(session) == "//"

    def test_special_characters(self):
        session = MagicMock()
        session.app_name = "app/name"
        session.user_id = "user:id"
        session.id = "sess id"
        assert get_memory_key(session) == "app/name/user:id/sess id"


class TestMakeMemoryKey:
    """Tests for make_memory_key."""

    def test_basic(self):
        assert make_memory_key("app", "user", "session") == "app/user/session"

    def test_empty_strings(self):
        assert make_memory_key("", "", "") == "//"

    def test_with_special_chars(self):
        assert make_memory_key("a:b", "c:d", "e:f") == "a:b/c:d/e:f"


class TestGetMemoryKeyFromSaveKey:
    """Tests for get_memory_key_from_save_key."""

    def test_replaces_colons(self):
        assert get_memory_key_from_save_key("a:b:c") == "a_b_c"

    def test_no_colons(self):
        assert get_memory_key_from_save_key("abc") == "abc"

    def test_empty_string(self):
        assert get_memory_key_from_save_key("") == ""

    def test_multiple_colons(self):
        assert get_memory_key_from_save_key("x:y:z:w") == "x_y_z_w"


class TestGetMemoryKeyFromSession:
    """Tests for get_memory_key_from_session."""

    def test_matches_get_memory_key(self):
        session = MagicMock()
        session.app_name = "app"
        session.user_id = "uid"
        session.id = "sid"
        assert get_memory_key_from_session(session) == "app/uid/sid"
        assert get_memory_key_from_session(session) == get_memory_key(session)


class TestAgentContextVar:
    """Tests for get_agent_context / set_agent_context."""

    def setup_method(self):
        _agent_context.set(None)

    def test_default_is_none(self):
        assert get_agent_context() is None

    def test_set_and_get(self):
        ctx = AgentContext()
        set_agent_context(ctx)
        assert get_agent_context() is ctx

    def test_overwrite(self):
        ctx1 = AgentContext()
        ctx2 = AgentContext()
        set_agent_context(ctx1)
        assert get_agent_context() is ctx1
        set_agent_context(ctx2)
        assert get_agent_context() is ctx2

    def test_set_none(self):
        ctx = AgentContext()
        set_agent_context(ctx)
        set_agent_context(None)
        assert get_agent_context() is None
