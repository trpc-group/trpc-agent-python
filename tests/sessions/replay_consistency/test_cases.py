# Tencent is pleased to support the open source community by making
# tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Unit tests for replay case definitions and JSONL fixture loading."""

from __future__ import annotations

import os
import json
import pathlib

import pytest


class TestReplayCases:
    """Tests for replay case definitions."""

    def test_all_10_cases_defined(self):
        """Exactly 10 replay cases must be defined."""
        from tests.sessions.replay_consistency.cases import _replay_cases
        cases = _replay_cases()
        assert len(cases) == 10, f"Expected 10 cases, got {len(cases)}"

    def test_each_case_has_required_fields(self):
        """Every case must have name, app_name, user_id, session_id."""
        from tests.sessions.replay_consistency.cases import _replay_cases
        for case in _replay_cases():
            assert case.name, f"Case missing name"
            assert case.app_name, f"Case {case.name} missing app_name"
            assert case.user_id, f"Case {case.name} missing user_id"
            assert case.session_id, f"Case {case.name} missing session_id"

    def test_at_least_one_case_has_tool_call(self):
        """At least one case should include tool_call events."""
        from tests.sessions.replay_consistency.cases import _replay_cases
        found = False
        for case in _replay_cases():
            for evt in case.events:
                if evt.tool_calls:
                    found = True
                    break
        assert found, "No case includes tool_call events"

    def test_at_least_one_case_has_summary_steps(self):
        """At least one case should exercise summary generation."""
        from tests.sessions.replay_consistency.cases import _replay_cases
        found = False
        for case in _replay_cases():
            if case.summary_steps:
                found = True
                break
        assert found, "No case includes summary steps"

    def test_at_least_one_case_has_track_events(self):
        """At least one case should exercise track events."""
        from tests.sessions.replay_consistency.cases import _replay_cases
        found = False
        for case in _replay_cases():
            if case.track_events:
                found = True
                break
        assert found, "No case includes track events"


class TestJSONLFixtures:
    """Tests for JSONL fixture loading."""

    @pytest.fixture
    def fixtures_dir(self) -> pathlib.Path:
        return pathlib.Path(__file__).parent / "fixtures"

    def test_all_10_jsonl_fixtures_exist(self, fixtures_dir):
        """All 10 JSONL fixture files must be present."""
        expected = [
            "case_001_single_turn.jsonl",
            "case_002_multi_turn.jsonl",
            "case_003_tool_call.jsonl",
            "case_004_state_updates.jsonl",
            "case_005_memory_rw.jsonl",
            "case_006_summary.jsonl",
            "case_007_summary_truncation.jsonl",
            "case_008_track_events.jsonl",
            "case_009_concurrent_writes.jsonl",
            "case_010_error_recovery.jsonl",
        ]
        for name in expected:
            path = fixtures_dir / name
            assert path.exists(), f"Missing fixture: {name}"

    def test_can_load_case_from_jsonl(self, fixtures_dir):
        """Each JSONL fixture must be loadable into a ReplayCase."""
        from tests.sessions.replay_consistency.cases import load_case_from_jsonl
        path = fixtures_dir / "case_001_single_turn.jsonl"
        case = load_case_from_jsonl(str(path))
        assert case.name == "single_turn_text"
        assert len(case.events) > 0
        assert len(case.memory_writes) > 0

    def test_roundtrip_case_to_jsonl(self, fixtures_dir):
        """Saving and reloading a case should produce identical data."""
        from tests.sessions.replay_consistency.cases import _replay_cases, save_case_to_jsonl, load_case_from_jsonl
        import tempfile
        for case in _replay_cases():
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".jsonl", delete=False
            ) as f:
                save_case_to_jsonl(case, f.name)
                f.flush()
                reloaded = load_case_from_jsonl(f.name)
            assert reloaded.name == case.name
            assert reloaded.session_id == case.session_id
            assert len(reloaded.events) == len(case.events)
            os.unlink(f.name)

    def test_jsonl_format_is_valid_json(self, fixtures_dir):
        """Each line in JSONL must be valid JSON."""
        for path in fixtures_dir.glob("case_*.jsonl"):
            with open(path, "r", encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        json.loads(line)
                    except json.JSONDecodeError as e:
                        pytest.fail(
                            f"{path.name}:{i} invalid JSON: {e}"
                        )
