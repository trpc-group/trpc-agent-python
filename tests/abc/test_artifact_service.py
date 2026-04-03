# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.abc._artifact_service concrete data models.

Covers:
- ArtifactVersion: fields, defaults, serialization
- ArtifactId: fields, defaults, serialization
- ArtifactEntry: dataclass construction
"""

from __future__ import annotations

import time
from datetime import datetime

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.abc._artifact_service import (
    ArtifactEntry,
    ArtifactId,
    ArtifactVersion,
)

from google.genai.types import Part


class TestArtifactVersion:
    """Tests for ArtifactVersion Pydantic model."""

    def test_required_fields(self):
        v = ArtifactVersion(version=1, canonical_uri="gs://bucket/path")
        assert v.version == 1
        assert v.canonical_uri == "gs://bucket/path"

    def test_custom_metadata_defaults_to_empty(self):
        v = ArtifactVersion(version=1, canonical_uri="uri")
        assert v.custom_metadata == {}

    def test_custom_metadata_set(self):
        v = ArtifactVersion(
            version=2, canonical_uri="uri",
            custom_metadata={"author": "test"},
        )
        assert v.custom_metadata == {"author": "test"}

    def test_create_time_defaults_to_now(self):
        before = datetime.now().timestamp()
        v = ArtifactVersion(version=1, canonical_uri="uri")
        after = datetime.now().timestamp()
        assert before <= v.create_time <= after

    def test_create_time_custom(self):
        v = ArtifactVersion(version=1, canonical_uri="uri", create_time=1000.0)
        assert v.create_time == 1000.0

    def test_mime_type_defaults_to_none(self):
        v = ArtifactVersion(version=1, canonical_uri="uri")
        assert v.mime_type is None

    def test_mime_type_set(self):
        v = ArtifactVersion(
            version=1, canonical_uri="uri", mime_type="application/pdf",
        )
        assert v.mime_type == "application/pdf"

    def test_camel_case_alias_serialization(self):
        v = ArtifactVersion(
            version=1, canonical_uri="uri",
            custom_metadata={"k": "v"}, mime_type="text/plain",
        )
        data = v.model_dump(by_alias=True)
        assert "canonicalUri" in data
        assert "customMetadata" in data
        assert "createTime" in data
        assert "mimeType" in data

    def test_populate_by_alias(self):
        v = ArtifactVersion(
            version=1, canonicalUri="uri", mimeType="image/png",
        )
        assert v.canonical_uri == "uri"
        assert v.mime_type == "image/png"


class TestArtifactId:
    """Tests for ArtifactId Pydantic model."""

    def test_required_fields(self):
        aid = ArtifactId(app_name="app", user_id="user")
        assert aid.app_name == "app"
        assert aid.user_id == "user"

    def test_session_id_defaults_to_none(self):
        aid = ArtifactId(app_name="app", user_id="user")
        assert aid.session_id is None

    def test_filename_defaults_to_empty(self):
        aid = ArtifactId(app_name="app", user_id="user")
        assert aid.filename == ""

    def test_custom_values(self):
        aid = ArtifactId(
            app_name="app", user_id="user",
            session_id="s1", filename="report.pdf",
        )
        assert aid.session_id == "s1"
        assert aid.filename == "report.pdf"

    def test_camel_case_alias_serialization(self):
        aid = ArtifactId(app_name="app", user_id="u", session_id="s")
        data = aid.model_dump(by_alias=True)
        assert "appName" in data
        assert "userId" in data
        assert "sessionId" in data

    def test_populate_by_alias(self):
        aid = ArtifactId(appName="app", userId="u", sessionId="s1")
        assert aid.app_name == "app"
        assert aid.user_id == "u"
        assert aid.session_id == "s1"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            ArtifactId(app_name="app")


class TestArtifactEntry:
    """Tests for ArtifactEntry dataclass."""

    def test_creation(self):
        part = Part(text="hello")
        version = ArtifactVersion(version=1, canonical_uri="uri")
        entry = ArtifactEntry(data=part, version=version)
        assert entry.data is part
        assert entry.version is version

    def test_fields_are_accessible(self):
        part = Part(text="data")
        version = ArtifactVersion(version=3, canonical_uri="gs://b/p")
        entry = ArtifactEntry(data=part, version=version)
        assert entry.version.version == 3
        assert entry.version.canonical_uri == "gs://b/p"
        assert entry.data.text == "data"
