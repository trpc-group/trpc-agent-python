# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.artifacts._in_memory_artifact_service.

Covers:
- InMemoryArtifactService.save_artifact (inline_data, text, file_data, artifact ref, metadata, errors)
- InMemoryArtifactService.load_artifact (existing, missing, version selection, ref resolution, empty data)
- InMemoryArtifactService.list_artifact_keys (session-scoped, user-scoped)
- InMemoryArtifactService.delete_artifact (existing, missing)
- InMemoryArtifactService.list_versions
- InMemoryArtifactService.list_artifact_versions
- InMemoryArtifactService.get_artifact_version
"""

from __future__ import annotations

import pytest

from google.genai import types

from trpc_agent_sdk.abc import ArtifactEntry, ArtifactId, ArtifactVersion
from trpc_agent_sdk.artifacts._in_memory_artifact_service import InMemoryArtifactService


def _make_id(
    app_name: str = "app",
    user_id: str = "u1",
    session_id: str = "s1",
    filename: str = "file.txt",
) -> ArtifactId:
    return ArtifactId(app_name=app_name, user_id=user_id, session_id=session_id, filename=filename)


def _text_part(text: str = "hello world") -> types.Part:
    return types.Part(text=text)


def _inline_part(data: bytes = b"binary content", mime_type: str = "application/octet-stream") -> types.Part:
    return types.Part(inline_data=types.Blob(data=data, mime_type=mime_type))


def _file_data_part(file_uri: str = "gs://bucket/file.bin", mime_type: str = "image/png") -> types.Part:
    return types.Part(file_data=types.FileData(file_uri=file_uri, mime_type=mime_type))


def _artifact_ref_part(uri: str) -> types.Part:
    return types.Part(file_data=types.FileData(file_uri=uri, mime_type="application/octet-stream"))


# ---------------------------------------------------------------------------
# save_artifact
# ---------------------------------------------------------------------------


class TestSaveArtifact:
    @pytest.mark.asyncio
    async def test_save_text_artifact(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        version = await svc.save_artifact(artifact_id=aid, artifact=_text_part())
        assert version == 0

    @pytest.mark.asyncio
    async def test_save_text_sets_mime_type(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part())
        entry = await svc.load_artifact(artifact_id=aid)
        assert entry.artifact_version.mime_type == "text/plain"

    @pytest.mark.asyncio
    async def test_save_inline_data_artifact(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        version = await svc.save_artifact(artifact_id=aid, artifact=_inline_part())
        assert version == 0

    @pytest.mark.asyncio
    async def test_save_inline_data_preserves_mime_type(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_inline_part(mime_type="image/jpeg"))
        entry = await svc.load_artifact(artifact_id=aid)
        assert entry.artifact_version.mime_type == "image/jpeg"

    @pytest.mark.asyncio
    async def test_save_file_data_artifact(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        version = await svc.save_artifact(artifact_id=aid, artifact=_file_data_part())
        assert version == 0

    @pytest.mark.asyncio
    async def test_save_file_data_preserves_mime_type(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_file_data_part(mime_type="image/png"))
        entry = await svc.load_artifact(artifact_id=aid)
        assert entry.artifact_version.mime_type == "image/png"

    @pytest.mark.asyncio
    async def test_save_artifact_ref_valid(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        ref_uri = "artifact://apps/app/users/u1/sessions/s1/artifacts/target.txt/versions/0"
        version = await svc.save_artifact(artifact_id=aid, artifact=_artifact_ref_part(ref_uri))
        assert version == 0

    @pytest.mark.asyncio
    async def test_save_artifact_ref_invalid_raises(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        bad_ref = types.Part(
            file_data=types.FileData(
                file_uri="artifact://invalid_format",
                mime_type="application/octet-stream",
            )
        )
        with pytest.raises(ValueError, match="Invalid artifact reference URI"):
            await svc.save_artifact(artifact_id=aid, artifact=bad_ref)

    @pytest.mark.asyncio
    async def test_save_unsupported_type_raises(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        empty_part = types.Part()
        with pytest.raises(ValueError, match="Not supported artifact type"):
            await svc.save_artifact(artifact_id=aid, artifact=empty_part)

    @pytest.mark.asyncio
    async def test_save_with_metadata(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        meta = {"author": "test", "tags": ["important"]}
        await svc.save_artifact(artifact_id=aid, artifact=_text_part(), metadata=meta)
        entry = await svc.load_artifact(artifact_id=aid)
        assert entry.artifact_version.custom_metadata == meta

    @pytest.mark.asyncio
    async def test_save_without_metadata_has_empty_dict(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part())
        entry = await svc.load_artifact(artifact_id=aid)
        assert entry.artifact_version.custom_metadata == {}

    @pytest.mark.asyncio
    async def test_multiple_saves_increment_version(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        v0 = await svc.save_artifact(artifact_id=aid, artifact=_text_part("v0"))
        v1 = await svc.save_artifact(artifact_id=aid, artifact=_text_part("v1"))
        v2 = await svc.save_artifact(artifact_id=aid, artifact=_text_part("v2"))
        assert v0 == 0
        assert v1 == 1
        assert v2 == 2

    @pytest.mark.asyncio
    async def test_save_creates_canonical_uri(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part())
        entry = await svc.load_artifact(artifact_id=aid)
        assert entry.artifact_version.canonical_uri.startswith("memory://")
        assert "/versions/0" in entry.artifact_version.canonical_uri


# ---------------------------------------------------------------------------
# load_artifact
# ---------------------------------------------------------------------------


class TestLoadArtifact:
    @pytest.mark.asyncio
    async def test_load_existing_returns_entry(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("hello"))
        entry = await svc.load_artifact(artifact_id=aid)
        assert entry is not None
        assert entry.data.text == "hello"

    @pytest.mark.asyncio
    async def test_load_nonexistent_returns_none(self):
        svc = InMemoryArtifactService()
        aid = _make_id(filename="nonexistent.txt")
        assert await svc.load_artifact(artifact_id=aid) is None

    @pytest.mark.asyncio
    async def test_load_latest_version_by_default(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v0"))
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v1"))
        entry = await svc.load_artifact(artifact_id=aid)
        assert entry.data.text == "v1"

    @pytest.mark.asyncio
    async def test_load_specific_version(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v0"))
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v1"))
        entry = await svc.load_artifact(artifact_id=aid, version=0)
        assert entry.data.text == "v0"

    @pytest.mark.asyncio
    async def test_load_out_of_range_version_returns_none(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part())
        assert await svc.load_artifact(artifact_id=aid, version=999) is None

    @pytest.mark.asyncio
    async def test_load_empty_part_returns_none(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        svc.artifacts["app/u1/s1/file.txt"] = [
            ArtifactEntry(
                data=types.Part(),
                version=ArtifactVersion(version=0, canonical_uri="memory://test"),
            )
        ]
        assert await svc.load_artifact(artifact_id=aid) is None

    @pytest.mark.asyncio
    async def test_load_empty_text_part_returns_none(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        svc.artifacts["app/u1/s1/file.txt"] = [
            ArtifactEntry(
                data=types.Part(text=""),
                version=ArtifactVersion(version=0, canonical_uri="memory://test"),
            )
        ]
        assert await svc.load_artifact(artifact_id=aid) is None

    @pytest.mark.asyncio
    async def test_load_empty_inline_data_returns_none(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        svc.artifacts["app/u1/s1/file.txt"] = [
            ArtifactEntry(
                data=types.Part(inline_data=types.Blob(data=b"", mime_type="text/plain")),
                version=ArtifactVersion(version=0, canonical_uri="memory://test"),
            )
        ]
        assert await svc.load_artifact(artifact_id=aid) is None

    @pytest.mark.asyncio
    async def test_load_resolves_artifact_reference(self):
        svc = InMemoryArtifactService()
        target_id = _make_id(filename="target.txt")
        await svc.save_artifact(artifact_id=target_id, artifact=_text_part("resolved content"))

        ref_id = _make_id(filename="ref.txt")
        ref_uri = "artifact://apps/app/users/u1/sessions/s1/artifacts/target.txt/versions/0"
        await svc.save_artifact(artifact_id=ref_id, artifact=_artifact_ref_part(ref_uri))

        entry = await svc.load_artifact(artifact_id=ref_id)
        assert entry is not None
        assert entry.data.text == "resolved content"

    @pytest.mark.asyncio
    async def test_load_artifact_ref_with_invalid_uri_raises(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        svc.artifacts["app/u1/s1/file.txt"] = [
            ArtifactEntry(
                data=types.Part(
                    file_data=types.FileData(
                        file_uri="artifact://bad",
                        mime_type="text/plain",
                    )
                ),
                version=ArtifactVersion(version=0, canonical_uri="memory://test"),
            )
        ]
        with pytest.raises(ValueError, match="Invalid artifact reference URI"):
            await svc.load_artifact(artifact_id=aid)

    @pytest.mark.asyncio
    async def test_load_artifact_ref_to_missing_target_returns_none(self):
        svc = InMemoryArtifactService()
        ref_id = _make_id(filename="ref.txt")
        ref_uri = "artifact://apps/app/users/u1/sessions/s1/artifacts/missing.txt/versions/0"
        await svc.save_artifact(artifact_id=ref_id, artifact=_artifact_ref_part(ref_uri))

        entry = await svc.load_artifact(artifact_id=ref_id)
        assert entry is None

    @pytest.mark.asyncio
    async def test_load_inline_data_artifact(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_inline_part(b"data123"))
        entry = await svc.load_artifact(artifact_id=aid)
        assert entry is not None
        assert entry.data.inline_data.data == b"data123"

    @pytest.mark.asyncio
    async def test_load_none_entry_in_list_returns_none(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        svc.artifacts["app/u1/s1/file.txt"] = [None]
        assert await svc.load_artifact(artifact_id=aid) is None


# ---------------------------------------------------------------------------
# list_artifact_keys
# ---------------------------------------------------------------------------


class TestListArtifactKeys:
    @pytest.mark.asyncio
    async def test_empty_service(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        keys = await svc.list_artifact_keys(artifact_id=aid)
        assert keys == []

    @pytest.mark.asyncio
    async def test_session_scoped_keys(self):
        svc = InMemoryArtifactService()
        aid1 = _make_id(filename="a.txt")
        aid2 = _make_id(filename="b.txt")
        await svc.save_artifact(artifact_id=aid1, artifact=_text_part())
        await svc.save_artifact(artifact_id=aid2, artifact=_text_part())

        keys = await svc.list_artifact_keys(artifact_id=_make_id())
        assert "a.txt" in keys
        assert "b.txt" in keys

    @pytest.mark.asyncio
    async def test_user_scoped_keys(self):
        svc = InMemoryArtifactService()
        aid = _make_id(filename="user:global.txt")
        await svc.save_artifact(artifact_id=aid, artifact=_text_part())

        keys = await svc.list_artifact_keys(artifact_id=_make_id())
        assert "user:global.txt" in keys

    @pytest.mark.asyncio
    async def test_mixed_keys_sorted(self):
        svc = InMemoryArtifactService()
        await svc.save_artifact(artifact_id=_make_id(filename="z.txt"), artifact=_text_part())
        await svc.save_artifact(artifact_id=_make_id(filename="a.txt"), artifact=_text_part())
        await svc.save_artifact(artifact_id=_make_id(filename="m.txt"), artifact=_text_part())

        keys = await svc.list_artifact_keys(artifact_id=_make_id())
        assert keys == sorted(keys)

    @pytest.mark.asyncio
    async def test_different_session_keys_not_listed(self):
        svc = InMemoryArtifactService()
        await svc.save_artifact(artifact_id=_make_id(session_id="s1", filename="a.txt"), artifact=_text_part())
        await svc.save_artifact(artifact_id=_make_id(session_id="s2", filename="b.txt"), artifact=_text_part())

        keys = await svc.list_artifact_keys(artifact_id=_make_id(session_id="s1"))
        assert "a.txt" in keys
        assert "b.txt" not in keys

    @pytest.mark.asyncio
    async def test_user_scoped_visible_across_sessions(self):
        svc = InMemoryArtifactService()
        await svc.save_artifact(artifact_id=_make_id(session_id="s1", filename="user:shared.txt"), artifact=_text_part())

        keys_s1 = await svc.list_artifact_keys(artifact_id=_make_id(session_id="s1"))
        keys_s2 = await svc.list_artifact_keys(artifact_id=_make_id(session_id="s2"))
        assert "user:shared.txt" in keys_s1
        assert "user:shared.txt" in keys_s2

    @pytest.mark.asyncio
    async def test_no_session_id_lists_user_scoped_only(self):
        svc = InMemoryArtifactService()
        await svc.save_artifact(artifact_id=_make_id(filename="session_file.txt"), artifact=_text_part())
        await svc.save_artifact(artifact_id=_make_id(filename="user:global.txt"), artifact=_text_part())

        keys = await svc.list_artifact_keys(artifact_id=_make_id(session_id=None))
        assert "user:global.txt" in keys
        assert "session_file.txt" not in keys


# ---------------------------------------------------------------------------
# delete_artifact
# ---------------------------------------------------------------------------


class TestDeleteArtifact:
    @pytest.mark.asyncio
    async def test_delete_existing(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part())
        assert await svc.load_artifact(artifact_id=aid) is not None

        await svc.delete_artifact(artifact_id=aid)
        assert await svc.load_artifact(artifact_id=aid) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_no_error(self):
        svc = InMemoryArtifactService()
        aid = _make_id(filename="nonexistent.txt")
        await svc.delete_artifact(artifact_id=aid)

    @pytest.mark.asyncio
    async def test_delete_removes_all_versions(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v0"))
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v1"))

        await svc.delete_artifact(artifact_id=aid)
        assert await svc.list_versions(artifact_id=aid) == []

    @pytest.mark.asyncio
    async def test_delete_one_does_not_affect_others(self):
        svc = InMemoryArtifactService()
        aid1 = _make_id(filename="a.txt")
        aid2 = _make_id(filename="b.txt")
        await svc.save_artifact(artifact_id=aid1, artifact=_text_part())
        await svc.save_artifact(artifact_id=aid2, artifact=_text_part())

        await svc.delete_artifact(artifact_id=aid1)
        assert await svc.load_artifact(artifact_id=aid1) is None
        assert await svc.load_artifact(artifact_id=aid2) is not None


# ---------------------------------------------------------------------------
# list_versions
# ---------------------------------------------------------------------------


class TestListVersions:
    @pytest.mark.asyncio
    async def test_no_versions(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        assert await svc.list_versions(artifact_id=aid) == []

    @pytest.mark.asyncio
    async def test_single_version(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part())
        assert await svc.list_versions(artifact_id=aid) == [0]

    @pytest.mark.asyncio
    async def test_multiple_versions(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        for i in range(5):
            await svc.save_artifact(artifact_id=aid, artifact=_text_part(f"v{i}"))
        assert await svc.list_versions(artifact_id=aid) == [0, 1, 2, 3, 4]


# ---------------------------------------------------------------------------
# list_artifact_versions
# ---------------------------------------------------------------------------


class TestListArtifactVersions:
    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        assert await svc.list_artifact_versions(artifact_id=aid) == []

    @pytest.mark.asyncio
    async def test_returns_artifact_version_objects(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v0"))
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v1"))

        versions = await svc.list_artifact_versions(artifact_id=aid)
        assert len(versions) == 2
        assert all(isinstance(v, ArtifactVersion) for v in versions)
        assert versions[0].version == 0
        assert versions[1].version == 1

    @pytest.mark.asyncio
    async def test_versions_have_canonical_uri(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part())
        versions = await svc.list_artifact_versions(artifact_id=aid)
        assert versions[0].canonical_uri.startswith("memory://")

    @pytest.mark.asyncio
    async def test_versions_preserve_metadata(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part(), metadata={"key": "val"})
        versions = await svc.list_artifact_versions(artifact_id=aid)
        assert versions[0].custom_metadata == {"key": "val"}


# ---------------------------------------------------------------------------
# get_artifact_version
# ---------------------------------------------------------------------------


class TestGetArtifactVersion:
    @pytest.mark.asyncio
    async def test_no_artifact_returns_none(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        assert await svc.get_artifact_version(artifact_id=aid) is None

    @pytest.mark.asyncio
    async def test_latest_version_default(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v0"))
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v1"))
        av = await svc.get_artifact_version(artifact_id=aid)
        assert av is not None
        assert av.version == 1

    @pytest.mark.asyncio
    async def test_specific_version(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v0"))
        await svc.save_artifact(artifact_id=aid, artifact=_text_part("v1"))
        av = await svc.get_artifact_version(artifact_id=aid, version=0)
        assert av is not None
        assert av.version == 0

    @pytest.mark.asyncio
    async def test_out_of_range_returns_none(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part())
        assert await svc.get_artifact_version(artifact_id=aid, version=999) is None

    @pytest.mark.asyncio
    async def test_returns_artifact_version_type(self):
        svc = InMemoryArtifactService()
        aid = _make_id()
        await svc.save_artifact(artifact_id=aid, artifact=_text_part())
        av = await svc.get_artifact_version(artifact_id=aid, version=0)
        assert isinstance(av, ArtifactVersion)


# ---------------------------------------------------------------------------
# Integration / cross-method tests
# ---------------------------------------------------------------------------


class TestIntegration:
    @pytest.mark.asyncio
    async def test_save_load_delete_lifecycle(self):
        svc = InMemoryArtifactService()
        aid = _make_id()

        v = await svc.save_artifact(artifact_id=aid, artifact=_text_part("hello"))
        assert v == 0

        entry = await svc.load_artifact(artifact_id=aid)
        assert entry.data.text == "hello"

        await svc.delete_artifact(artifact_id=aid)
        assert await svc.load_artifact(artifact_id=aid) is None
        assert await svc.list_versions(artifact_id=aid) == []

    @pytest.mark.asyncio
    async def test_multiple_artifacts_independent(self):
        svc = InMemoryArtifactService()
        aid1 = _make_id(filename="f1.txt")
        aid2 = _make_id(filename="f2.txt")

        await svc.save_artifact(artifact_id=aid1, artifact=_text_part("file1"))
        await svc.save_artifact(artifact_id=aid2, artifact=_text_part("file2"))

        e1 = await svc.load_artifact(artifact_id=aid1)
        e2 = await svc.load_artifact(artifact_id=aid2)
        assert e1.data.text == "file1"
        assert e2.data.text == "file2"

    @pytest.mark.asyncio
    async def test_user_scoped_artifact_lifecycle(self):
        svc = InMemoryArtifactService()
        aid = _make_id(filename="user:profile.json")

        await svc.save_artifact(artifact_id=aid, artifact=_text_part('{"name": "test"}'))
        entry = await svc.load_artifact(artifact_id=aid)
        assert entry is not None
        assert '{"name": "test"}' in entry.data.text

        keys = await svc.list_artifact_keys(artifact_id=_make_id())
        assert "user:profile.json" in keys

    @pytest.mark.asyncio
    async def test_fresh_service_is_empty(self):
        svc = InMemoryArtifactService()
        assert svc.artifacts == {}
