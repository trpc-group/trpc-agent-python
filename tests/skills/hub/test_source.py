# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.hub._source.

Covers:
- SkillSource cannot be instantiated directly (ABC contract)
- A subclass missing any abstract method also cannot be instantiated
- A subclass implementing all four methods can be instantiated and used
"""

from __future__ import annotations

import pytest

from trpc_agent_sdk.skills.hub import SkillBundle
from trpc_agent_sdk.skills.hub import SkillMeta
from trpc_agent_sdk.skills.hub import SkillSource


class TestSkillSourceContract:

    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            SkillSource()  # type: ignore[abstract]

    def test_subclass_missing_methods_cannot_instantiate(self):

        class Incomplete(SkillSource):

            def source_id(self) -> str:
                return "incomplete"

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_complete_subclass_is_instantiable(self):

        class Complete(SkillSource):

            def source_id(self) -> str:
                return "complete"

            def search(self, query: str, limit: int = 10) -> list[SkillMeta]:
                return []

            def inspect(self, identifier: str) -> SkillMeta | None:
                return None

            def fetch(self, identifier: str) -> SkillBundle | None:
                return None

        source = Complete()
        assert source.source_id() == "complete"
        assert source.search("q") == []
        assert source.inspect("id") is None
        assert source.fetch("id") is None
        assert isinstance(source, SkillSource)
