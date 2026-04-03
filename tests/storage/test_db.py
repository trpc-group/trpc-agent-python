# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for BaseStorage abstract class."""

from abc import ABC

import pytest

from trpc_agent_sdk.storage._db import BaseStorage


class TestBaseStorageAbstract:

    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BaseStorage()

    def test_is_abstract_class(self):
        assert issubclass(BaseStorage, ABC)

    def test_has_abstract_methods(self):
        abstract_methods = BaseStorage.__abstractmethods__
        expected = {"add", "delete", "query", "get", "commit", "refresh", "close"}
        assert abstract_methods == expected


class TestBaseStorageSubclass:

    def test_incomplete_subclass_raises(self):
        class PartialStorage(BaseStorage):
            async def add(self, db, data):
                pass

        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            PartialStorage()

    def test_complete_subclass_instantiates(self):
        class ConcreteStorage(BaseStorage):
            async def add(self, db, data):
                pass

            async def delete(self, db, key):
                pass

            async def query(self, db, key, filters, limit=None):
                pass

            async def get(self, db, key):
                pass

            async def commit(self, db, data):
                pass

            async def refresh(self, db, data):
                pass

            async def close(self):
                pass

        storage = ConcreteStorage()
        assert isinstance(storage, BaseStorage)

    @pytest.mark.asyncio
    async def test_subclass_methods_callable(self):
        class ConcreteStorage(BaseStorage):
            async def add(self, db, data):
                return "added"

            async def delete(self, db, key):
                return "deleted"

            async def query(self, db, key, filters, limit=None):
                return ["result"]

            async def get(self, db, key):
                return "value"

            async def commit(self, db, data):
                return "committed"

            async def refresh(self, db, data):
                return "refreshed"

            async def close(self):
                return "closed"

        storage = ConcreteStorage()
        assert await storage.add(None, None) == "added"
        assert await storage.delete(None, None) == "deleted"
        assert await storage.query(None, None, []) == ["result"]
        assert await storage.get(None, None) == "value"
        assert await storage.commit(None, None) == "committed"
        assert await storage.refresh(None, None) == "refreshed"
        assert await storage.close() == "closed"


class TestBaseStorageReexport:

    def test_reexported_from_package(self):
        from trpc_agent_sdk.storage import BaseStorage as PkgBaseStorage

        assert PkgBaseStorage is BaseStorage
