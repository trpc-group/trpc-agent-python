# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for state mapping utilities."""

from trpc_agent_sdk.dsl.graph._state_mapper import StateMapper
from trpc_agent_sdk.dsl.graph._state_mapper import SubgraphResult


class TestStateMapperInput:
    """Tests for input mapping behavior."""

    def test_combine_merges_outputs_and_last_mapper_wins(self):
        """Combined mapper should merge outputs in declaration order."""
        state = {
            "query": "hello",
            "context": "world",
            "count": 2,
        }

        mapper = StateMapper.combine(
            StateMapper.pick("query", "context"),
            lambda current: {
                "query": current["query"].upper(),
                "count": current["count"] + 1,
            },
        )

        assert mapper(state) == {
            "query": "HELLO",
            "context": "world",
            "count": 3,
        }

    def test_rename_ignores_missing_source_fields(self):
        """Rename mapper should only emit keys that exist in source state."""
        mapper = StateMapper.rename({
            "source": "target",
            "missing": "ignored",
        })

        assert mapper({"source": "value"}) == {"target": "value"}

    def test_identity_returns_a_new_dict(self):
        """Identity mapper should not return the same mapping object."""
        source = {"value": 1}
        mapped = StateMapper.identity()(source)

        assert mapped == source
        assert mapped is not source

        mapped["value"] = 2
        assert source["value"] == 1

    def test_filter_and_exclude_select_expected_keys(self):
        """Key filtering and exclusion should preserve only intended fields."""
        state = {
            "user_id": "u-1",
            "user_name": "alice",
            "internal_secret": "token",
            "trace_id": "t-1",
        }

        only_user_fields = StateMapper.filter_keys(lambda key: key.startswith("user_"))(state)
        redacted = StateMapper.exclude("internal_secret")(state)

        assert only_user_fields == {
            "user_id": "u-1",
            "user_name": "alice",
        }
        assert redacted == {
            "user_id": "u-1",
            "user_name": "alice",
            "trace_id": "t-1",
        }


class TestStateMapperOutput:
    """Tests for output mapping behavior."""

    def test_merge_response_maps_child_last_response(self):
        """Output mapper should project child last response into target field."""
        mapper = StateMapper.merge_response("research_result")
        parent_state = {"request_id": "r-1"}
        child_result = SubgraphResult(last_response="final answer")

        mapped = mapper(parent_state, child_result)

        assert mapped == {"research_result": "final answer"}
        assert parent_state == {"request_id": "r-1"}
