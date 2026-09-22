# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for persistent filesystem artifact storage."""

from __future__ import annotations

import asyncio
from pathlib import Path

from google.genai.types import Blob
from google.genai.types import FileData
from google.genai.types import Part
import pytest

from trpc_agent_sdk.abc import ArtifactId
from trpc_agent_sdk.artifacts import FileArtifactService


def _artifact_id(
    *,
    app_name: str = "app",
    filename: str = "report.txt",
    session_id: str | None = "session-1",
    user_id: str = "user-1",
) -> ArtifactId:
    return ArtifactId(
        app_name=app_name,
        filename=filename,
        session_id=session_id,
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_save_load_versions_and_metadata(tmp_path: Path) -> None:
    service = FileArtifactService(tmp_path)
    artifact_id = _artifact_id()

    assert await service.save_artifact(
        artifact_id=artifact_id,
        artifact=Part(text="first"),
        metadata={"author": "Ada"},
    ) == 0
    assert await service.save_artifact(
        artifact_id=artifact_id,
        artifact=Part(text="second"),
        metadata={"author": "Grace"},
    ) == 1

    first = await service.load_artifact(artifact_id=artifact_id, version=0)
    latest = await service.load_artifact(artifact_id=artifact_id)
    assert first is not None
    assert first.data.text == "first"
    assert first.version.custom_metadata == {"author": "Ada"}
    assert first.version.mime_type == "text/plain"
    assert latest is not None
    assert latest.data.text == "second"
    assert latest.version.version == 1
    assert latest.version.canonical_uri.startswith("artifact://")
    assert await service.list_versions(artifact_id=artifact_id) == [0, 1]

    versions = await service.list_artifact_versions(artifact_id=artifact_id)
    assert [item.version for item in versions] == [0, 1]
    assert versions[1].custom_metadata == {"author": "Grace"}
    assert await service.get_artifact_version(artifact_id=artifact_id) == versions[1]
    assert await service.get_artifact_version(artifact_id=artifact_id, version=0) == versions[0]


@pytest.mark.asyncio
async def test_binary_and_file_data_round_trip(tmp_path: Path) -> None:
    service = FileArtifactService(tmp_path)
    binary_id = _artifact_id(filename="image.bin")
    file_id = _artifact_id(filename="remote.png")

    await service.save_artifact(
        artifact_id=binary_id,
        artifact=Part(inline_data=Blob(data=b"\x00\x01data", mime_type="application/octet-stream")),
    )
    await service.save_artifact(
        artifact_id=file_id,
        artifact=Part(file_data=FileData(file_uri="file:///tmp/image.png", mime_type="image/png")),
    )

    binary = await service.load_artifact(artifact_id=binary_id)
    file_data = await service.load_artifact(artifact_id=file_id)
    assert binary is not None
    assert binary.data.inline_data.data == b"\x00\x01data"
    assert binary.version.mime_type == "application/octet-stream"
    assert file_data is not None
    assert file_data.data.file_data.file_uri == "file:///tmp/image.png"
    assert file_data.version.mime_type == "image/png"


@pytest.mark.asyncio
async def test_restart_persistence(tmp_path: Path) -> None:
    artifact_id = _artifact_id()
    first_service = FileArtifactService(tmp_path)
    await first_service.save_artifact(artifact_id=artifact_id, artifact=Part(text="persistent"))

    restarted_service = FileArtifactService(tmp_path)
    entry = await restarted_service.load_artifact(artifact_id=artifact_id)
    assert entry is not None
    assert entry.data.text == "persistent"
    assert await restarted_service.list_versions(artifact_id=artifact_id) == [0]


@pytest.mark.asyncio
async def test_session_and_user_isolation(tmp_path: Path) -> None:
    service = FileArtifactService(tmp_path)
    first = _artifact_id(filename="first.txt")
    other_session = _artifact_id(filename="second.txt", session_id="session-2")
    other_user = _artifact_id(filename="third.txt", user_id="user-2")
    other_app = _artifact_id(app_name="other-app", filename="fourth.txt")
    for artifact_id in (first, other_session, other_user, other_app):
        await service.save_artifact(artifact_id=artifact_id, artifact=Part(text=artifact_id.filename))

    assert await service.list_artifact_keys(artifact_id=first) == ["first.txt"]
    assert await service.list_artifact_keys(artifact_id=other_session) == ["second.txt"]
    assert await service.list_artifact_keys(artifact_id=other_user) == ["third.txt"]
    assert await service.list_artifact_keys(artifact_id=other_app) == ["fourth.txt"]


@pytest.mark.asyncio
async def test_user_scoped_artifact_is_shared_across_sessions(tmp_path: Path) -> None:
    service = FileArtifactService(tmp_path)
    user_artifact = _artifact_id(filename="user:profile.json", session_id="session-1")
    await service.save_artifact(artifact_id=user_artifact, artifact=Part(text='{"theme":"dark"}'))

    from_other_session = _artifact_id(filename="user:profile.json", session_id="session-2")
    entry = await service.load_artifact(artifact_id=from_other_session)
    assert entry is not None
    assert entry.data.text == '{"theme":"dark"}'
    assert await service.list_artifact_keys(artifact_id=from_other_session) == ["user:profile.json"]
    assert await service.list_artifact_keys(artifact_id=_artifact_id(filename="", session_id=None)
                                            ) == ["user:profile.json"]


@pytest.mark.asyncio
async def test_delete_removes_all_versions_only_for_target(tmp_path: Path) -> None:
    service = FileArtifactService(tmp_path)
    target = _artifact_id(filename="target.txt")
    neighbor = _artifact_id(filename="neighbor.txt")
    await service.save_artifact(artifact_id=target, artifact=Part(text="v0"))
    await service.save_artifact(artifact_id=target, artifact=Part(text="v1"))
    await service.save_artifact(artifact_id=neighbor, artifact=Part(text="keep"))

    await service.delete_artifact(artifact_id=target)
    await service.delete_artifact(artifact_id=target)

    assert await service.load_artifact(artifact_id=target) is None
    assert await service.list_versions(artifact_id=target) == []
    assert await service.load_artifact(artifact_id=neighbor) is not None


@pytest.mark.asyncio
async def test_missing_and_invalid_versions_return_none(tmp_path: Path) -> None:
    service = FileArtifactService(tmp_path)
    artifact_id = _artifact_id()
    assert await service.load_artifact(artifact_id=artifact_id) is None
    await service.save_artifact(artifact_id=artifact_id, artifact=Part(text="v0"))
    assert await service.load_artifact(artifact_id=artifact_id, version=-1) is None
    assert await service.load_artifact(artifact_id=artifact_id, version=10) is None
    assert await service.get_artifact_version(artifact_id=artifact_id, version=10) is None


@pytest.mark.asyncio
async def test_artifact_reference_resolves_persisted_target(tmp_path: Path) -> None:
    service = FileArtifactService(tmp_path)
    target = _artifact_id(filename="target.txt")
    reference = _artifact_id(filename="reference.txt")
    await service.save_artifact(artifact_id=target, artifact=Part(text="resolved"))
    await service.save_artifact(
        artifact_id=reference,
        artifact=Part(file_data=FileData(
            file_uri="artifact://apps/app/users/user-1/sessions/session-1/"
            "artifacts/target.txt/versions/0",
            mime_type="application/octet-stream",
        )),
    )

    entry = await service.load_artifact(artifact_id=reference)
    assert entry is not None
    assert entry.data.text == "resolved"


@pytest.mark.asyncio
async def test_concurrent_saves_allocate_unique_versions(tmp_path: Path) -> None:
    artifact_id = _artifact_id()
    services = [FileArtifactService(tmp_path) for _ in range(20)]

    versions = await asyncio.gather(*[
        service.save_artifact(
            artifact_id=artifact_id,
            artifact=Part(text=f"value-{index}"),
            metadata={"index": index},
        ) for index, service in enumerate(services)
    ])

    assert sorted(versions) == list(range(20))
    assert await services[0].list_versions(artifact_id=artifact_id) == list(range(20))
    entries = [await services[0].load_artifact(artifact_id=artifact_id, version=version) for version in range(20)]
    assert all(entry is not None for entry in entries)
    assert {entry.version.custom_metadata["index"] for entry in entries} == set(range(20))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("app_name", "../app"),
        ("app_name", ".."),
        ("filename", "../../escape"),
        ("filename", "/absolute"),
        ("session_id", r"..\escape"),
        ("user_id", "users/other"),
    ],
)
async def test_path_escape_components_are_rejected(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    values = {
        "app_name": "app",
        "filename": "file.txt",
        "session_id": "session",
        "user_id": "user",
    }
    values[field] = value
    artifact_id = ArtifactId(**values)
    service = FileArtifactService(tmp_path)

    with pytest.raises(ValueError):
        await service.save_artifact(artifact_id=artifact_id, artifact=Part(text="unsafe"))
    assert not (tmp_path.parent / "escape").exists()


@pytest.mark.asyncio
async def test_symlink_cannot_escape_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    service = FileArtifactService(root)
    app_directory = root / "apps" / "app"
    app_directory.parent.mkdir()
    app_directory.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="escapes root_dir"):
        await service.save_artifact(artifact_id=_artifact_id(), artifact=Part(text="unsafe"))
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_symlink_cannot_redirect_to_another_scope(tmp_path: Path) -> None:
    service = FileArtifactService(tmp_path)
    real_app_directory = tmp_path / "apps" / "real-app"
    real_app_directory.mkdir(parents=True)
    linked_app_directory = tmp_path / "apps" / "app"
    linked_app_directory.symlink_to(real_app_directory, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic links"):
        await service.save_artifact(artifact_id=_artifact_id(), artifact=Part(text="unsafe"))
    assert list(real_app_directory.iterdir()) == []
