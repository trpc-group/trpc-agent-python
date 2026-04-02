# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.artifacts._utils.

Covers:
- ParsedArtifactUri NamedTuple fields
- parse_artifact_uri (session-scoped, user-scoped, invalid inputs)
- get_artifact_uri (session-scoped vs user-scoped)
- is_artifact_ref (artifact file_data detection)
- file_has_user_namespace (user: prefix detection)
- artifact_path (session-scoped vs user-namespace)
- create_artifact_uri (memory:// URI generation)
"""

from __future__ import annotations

import pytest

from google.genai import types

from trpc_agent_sdk.abc import ArtifactId
from trpc_agent_sdk.artifacts._utils import (
    ParsedArtifactUri,
    artifact_path,
    create_artifact_uri,
    file_has_user_namespace,
    get_artifact_uri,
    is_artifact_ref,
    parse_artifact_uri,
)


# ---------------------------------------------------------------------------
# ParsedArtifactUri
# ---------------------------------------------------------------------------


class TestParsedArtifactUri:
    def test_fields_accessible(self):
        uri = ParsedArtifactUri(
            app_name="app",
            user_id="u1",
            session_id="s1",
            filename="file.txt",
            version=3,
        )
        assert uri.app_name == "app"
        assert uri.user_id == "u1"
        assert uri.session_id == "s1"
        assert uri.filename == "file.txt"
        assert uri.version == 3

    def test_session_id_can_be_none(self):
        uri = ParsedArtifactUri(
            app_name="app",
            user_id="u1",
            session_id=None,
            filename="file.txt",
            version=0,
        )
        assert uri.session_id is None

    def test_tuple_indexing(self):
        uri = ParsedArtifactUri("app", "u1", "s1", "file.txt", 5)
        assert uri[0] == "app"
        assert uri[4] == 5

    def test_equality(self):
        a = ParsedArtifactUri("app", "u1", "s1", "f.txt", 1)
        b = ParsedArtifactUri("app", "u1", "s1", "f.txt", 1)
        assert a == b

    def test_inequality_on_version(self):
        a = ParsedArtifactUri("app", "u1", "s1", "f.txt", 1)
        b = ParsedArtifactUri("app", "u1", "s1", "f.txt", 2)
        assert a != b


# ---------------------------------------------------------------------------
# parse_artifact_uri
# ---------------------------------------------------------------------------


class TestParseArtifactUri:
    def test_session_scoped_uri(self):
        uri = "artifact://apps/my_app/users/user1/sessions/sess1/artifacts/report.pdf/versions/3"
        result = parse_artifact_uri(uri)
        assert result is not None
        assert result.app_name == "my_app"
        assert result.user_id == "user1"
        assert result.session_id == "sess1"
        assert result.filename == "report.pdf"
        assert result.version == 3

    def test_user_scoped_uri(self):
        uri = "artifact://apps/my_app/users/user1/artifacts/config.json/versions/0"
        result = parse_artifact_uri(uri)
        assert result is not None
        assert result.app_name == "my_app"
        assert result.user_id == "user1"
        assert result.session_id is None
        assert result.filename == "config.json"
        assert result.version == 0

    def test_empty_string_returns_none(self):
        assert parse_artifact_uri("") is None

    def test_none_like_empty(self):
        assert parse_artifact_uri("") is None

    def test_non_artifact_scheme_returns_none(self):
        assert parse_artifact_uri("http://example.com") is None

    def test_memory_scheme_returns_none(self):
        assert parse_artifact_uri("memory://apps/a/users/u/artifacts/f/versions/0") is None

    def test_partial_uri_returns_none(self):
        assert parse_artifact_uri("artifact://apps/my_app/users") is None

    def test_missing_version_returns_none(self):
        assert parse_artifact_uri("artifact://apps/a/users/u/sessions/s/artifacts/f") is None

    def test_version_is_integer(self):
        uri = "artifact://apps/a/users/u/artifacts/f/versions/42"
        result = parse_artifact_uri(uri)
        assert isinstance(result.version, int)
        assert result.version == 42

    def test_zero_version(self):
        uri = "artifact://apps/a/users/u/artifacts/f/versions/0"
        result = parse_artifact_uri(uri)
        assert result.version == 0

    def test_large_version_number(self):
        uri = "artifact://apps/a/users/u/artifacts/f/versions/99999"
        result = parse_artifact_uri(uri)
        assert result.version == 99999


# ---------------------------------------------------------------------------
# get_artifact_uri
# ---------------------------------------------------------------------------


class TestGetArtifactUri:
    def test_session_scoped(self):
        aid = ArtifactId(app_name="app1", user_id="u1", session_id="s1", filename="doc.txt")
        result = get_artifact_uri(aid, version=2)
        assert result == "artifact://apps/app1/users/u1/sessions/s1/artifacts/doc.txt/versions/2"

    def test_user_scoped(self):
        aid = ArtifactId(app_name="app1", user_id="u1", session_id=None, filename="doc.txt")
        result = get_artifact_uri(aid, version=0)
        assert result == "artifact://apps/app1/users/u1/artifacts/doc.txt/versions/0"

    def test_empty_session_id_is_user_scoped(self):
        aid = ArtifactId(app_name="app1", user_id="u1", session_id="", filename="doc.txt")
        result = get_artifact_uri(aid, version=1)
        assert result == "artifact://apps/app1/users/u1/artifacts/doc.txt/versions/1"

    def test_roundtrip_session_scoped(self):
        aid = ArtifactId(app_name="myapp", user_id="bob", session_id="sess42", filename="data.bin")
        uri = get_artifact_uri(aid, version=7)
        parsed = parse_artifact_uri(uri)
        assert parsed is not None
        assert parsed.app_name == "myapp"
        assert parsed.user_id == "bob"
        assert parsed.session_id == "sess42"
        assert parsed.filename == "data.bin"
        assert parsed.version == 7

    def test_roundtrip_user_scoped(self):
        aid = ArtifactId(app_name="myapp", user_id="alice", session_id=None, filename="cfg.yaml")
        uri = get_artifact_uri(aid, version=0)
        parsed = parse_artifact_uri(uri)
        assert parsed is not None
        assert parsed.session_id is None
        assert parsed.filename == "cfg.yaml"


# ---------------------------------------------------------------------------
# is_artifact_ref
# ---------------------------------------------------------------------------


class TestIsArtifactRef:
    def test_valid_artifact_ref(self):
        part = types.Part(
            file_data=types.FileData(
                file_uri="artifact://apps/a/users/u/artifacts/f/versions/0",
                mime_type="text/plain",
            )
        )
        assert is_artifact_ref(part) is True

    def test_non_artifact_uri(self):
        part = types.Part(
            file_data=types.FileData(
                file_uri="https://example.com/file.txt",
                mime_type="text/plain",
            )
        )
        assert is_artifact_ref(part) is False

    def test_no_file_data(self):
        part = types.Part(text="hello")
        assert is_artifact_ref(part) is False

    def test_file_data_without_file_uri(self):
        part = types.Part(file_data=types.FileData(mime_type="text/plain"))
        assert is_artifact_ref(part) is False

    def test_empty_file_uri(self):
        part = types.Part(
            file_data=types.FileData(file_uri="", mime_type="text/plain")
        )
        assert is_artifact_ref(part) is False

    def test_session_scoped_artifact_ref(self):
        part = types.Part(
            file_data=types.FileData(
                file_uri="artifact://apps/a/users/u/sessions/s/artifacts/f/versions/1",
                mime_type="application/pdf",
            )
        )
        assert is_artifact_ref(part) is True


# ---------------------------------------------------------------------------
# file_has_user_namespace
# ---------------------------------------------------------------------------


class TestFileHasUserNamespace:
    def test_user_prefix(self):
        assert file_has_user_namespace("user:shared_doc.txt") is True

    def test_no_user_prefix(self):
        assert file_has_user_namespace("regular_file.txt") is False

    def test_empty_string(self):
        assert file_has_user_namespace("") is False

    def test_user_prefix_with_colon_only(self):
        assert file_has_user_namespace("user:") is True

    def test_similar_but_not_prefix(self):
        assert file_has_user_namespace("myuser:file.txt") is False

    def test_uppercase_user_not_matching(self):
        assert file_has_user_namespace("User:file.txt") is False


# ---------------------------------------------------------------------------
# artifact_path
# ---------------------------------------------------------------------------


class TestArtifactPath:
    def test_session_scoped(self):
        aid = ArtifactId(app_name="app", user_id="u1", session_id="s1", filename="report.txt")
        assert artifact_path(aid) == "app/u1/s1/report.txt"

    def test_user_namespace(self):
        aid = ArtifactId(app_name="app", user_id="u1", session_id="s1", filename="user:shared.txt")
        assert artifact_path(aid) == "app/u1/user/user:shared.txt"

    def test_user_namespace_ignores_session(self):
        aid1 = ArtifactId(app_name="app", user_id="u1", session_id="s1", filename="user:data.bin")
        aid2 = ArtifactId(app_name="app", user_id="u1", session_id="s2", filename="user:data.bin")
        assert artifact_path(aid1) == artifact_path(aid2)

    def test_different_sessions_produce_different_paths(self):
        aid1 = ArtifactId(app_name="app", user_id="u1", session_id="s1", filename="f.txt")
        aid2 = ArtifactId(app_name="app", user_id="u1", session_id="s2", filename="f.txt")
        assert artifact_path(aid1) != artifact_path(aid2)


# ---------------------------------------------------------------------------
# create_artifact_uri
# ---------------------------------------------------------------------------


class TestCreateArtifactUri:
    def test_session_scoped(self):
        aid = ArtifactId(app_name="app", user_id="u1", session_id="s1", filename="output.csv")
        uri = create_artifact_uri(aid, version=5)
        assert uri == "memory://apps/app/users/u1/sessions/s1/artifacts/output.csv/versions/5"

    def test_user_namespace(self):
        aid = ArtifactId(app_name="app", user_id="u1", session_id="s1", filename="user:global.cfg")
        uri = create_artifact_uri(aid, version=0)
        assert uri == "memory://apps/app/users/u1/artifacts/user:global.cfg/versions/0"

    def test_version_zero(self):
        aid = ArtifactId(app_name="a", user_id="u", session_id="s", filename="f")
        uri = create_artifact_uri(aid, version=0)
        assert "/versions/0" in uri

    def test_uses_memory_scheme(self):
        aid = ArtifactId(app_name="a", user_id="u", session_id="s", filename="f")
        uri = create_artifact_uri(aid, version=1)
        assert uri.startswith("memory://")
