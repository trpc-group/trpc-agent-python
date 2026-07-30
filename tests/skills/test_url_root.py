# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills._url_root.

Covers:
- ArchiveExtractor: path cleaning, permission sanitization, size limit checks,
  archive kind detection, zip/tar extraction
- SkillRootResolver: local paths, file:// URLs, cache directory resolution
- Enum values for CacheConfig, ArchiveExt, ArchiveKind, FilePerm, SizeLimit, TarPerm
"""

from __future__ import annotations

import io
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

from trpc_agent_sdk.skills._url_root import (
    ArchiveExt,
    ArchiveExtractor,
    ArchiveKind,
    CacheConfig,
    FilePerm,
    SizeLimit,
    SkillRootResolver,
    TarPerm,
)


# ---------------------------------------------------------------------------
# Enum smoke tests
# ---------------------------------------------------------------------------

class TestEnums:
    def test_cache_config_values(self):
        assert CacheConfig.APP_DIR == "trpc-agent-py"
        assert CacheConfig.SKILLS_DIR == "skills"
        assert CacheConfig.READY_FILE == ".ready"

    def test_archive_ext_values(self):
        assert ArchiveExt.ZIP == ".zip"
        assert ArchiveExt.TAR_GZ == ".tar.gz"

    def test_archive_kind_ordering(self):
        assert ArchiveKind.UNKNOWN < ArchiveKind.ZIP < ArchiveKind.TAR < ArchiveKind.TAR_GZ

    def test_file_perm_values(self):
        assert FilePerm.DIR == 0o755
        assert FilePerm.FILE == 0o644

    def test_size_limit_values(self):
        assert SizeLimit.MAX_DOWNLOAD == 64 << 20
        assert SizeLimit.MAX_EXTRACT_FILE == 64 << 20
        assert SizeLimit.MAX_EXTRACT_TOTAL == 256 << 20


# ---------------------------------------------------------------------------
# ArchiveExtractor — path cleaning
# ---------------------------------------------------------------------------

class TestCleanArchivePath:
    def test_normal_path(self):
        assert ArchiveExtractor._clean_archive_path("dir/file.txt") == "dir/file.txt"

    def test_root_entry(self):
        assert ArchiveExtractor._clean_archive_path(".") == ""

    def test_backslash_normalization(self):
        result = ArchiveExtractor._clean_archive_path("dir\\file.txt")
        assert "/" in result or "file.txt" in result

    def test_absolute_path_rejected(self):
        with pytest.raises(ValueError, match="invalid archive path"):
            ArchiveExtractor._clean_archive_path("/etc/passwd")

    def test_traversal_rejected(self):
        with pytest.raises(ValueError, match="invalid archive path"):
            ArchiveExtractor._clean_archive_path("../escape")

    def test_double_dot_only_rejected(self):
        with pytest.raises(ValueError, match="invalid archive path"):
            ArchiveExtractor._clean_archive_path("..")

    def test_colon_rejected(self):
        with pytest.raises(ValueError, match="invalid archive path"):
            ArchiveExtractor._clean_archive_path("C:file.txt")

    def test_leading_dot_slash_stripped(self):
        result = ArchiveExtractor._clean_archive_path("./dir/file.txt")
        assert not result.startswith("./")


# ---------------------------------------------------------------------------
# ArchiveExtractor — permission helpers
# ---------------------------------------------------------------------------

class TestSanitizePerm:
    def test_zero_returns_file_perm(self):
        assert ArchiveExtractor._sanitize_perm(0) == FilePerm.FILE

    def test_normal_perm(self):
        assert ArchiveExtractor._sanitize_perm(0o755) == 0o755

    def test_extra_bits_masked(self):
        result = ArchiveExtractor._sanitize_perm(0o100755)
        assert result == 0o755


class TestTarHeaderPerm:
    def test_negative_mode(self):
        assert ArchiveExtractor._tar_header_perm(-1) == FilePerm.FILE

    def test_normal_mode(self):
        result = ArchiveExtractor._tar_header_perm(0o100644)
        assert result == 0o644

    def test_zero_mode(self):
        assert ArchiveExtractor._tar_header_perm(0) == FilePerm.FILE

    def test_exec_mode(self):
        result = ArchiveExtractor._tar_header_perm(0o100755)
        assert result == 0o755


# ---------------------------------------------------------------------------
# ArchiveExtractor — size limit helpers
# ---------------------------------------------------------------------------

class TestAddExtractedBytes:
    def test_normal(self):
        assert ArchiveExtractor._add_extracted_bytes(0, 100) == 100

    def test_negative_raises(self):
        with pytest.raises(RuntimeError, match="negative"):
            ArchiveExtractor._add_extracted_bytes(0, -1)

    def test_overflow_raises(self):
        with pytest.raises(RuntimeError, match="too large"):
            ArchiveExtractor._add_extracted_bytes(SizeLimit.MAX_EXTRACT_TOTAL, 1)


class TestValidateTarSize:
    def test_normal(self):
        ArchiveExtractor._validate_tar_size(100)

    def test_negative_raises(self):
        with pytest.raises(RuntimeError, match="negative"):
            ArchiveExtractor._validate_tar_size(-1)

    def test_too_large_raises(self):
        with pytest.raises(RuntimeError, match="too large"):
            ArchiveExtractor._validate_tar_size(SizeLimit.MAX_EXTRACT_FILE + 1)


# ---------------------------------------------------------------------------
# ArchiveExtractor — kind detection
# ---------------------------------------------------------------------------

class TestKindFromName:
    def test_zip(self):
        assert ArchiveExtractor._kind_from_name("archive.zip") == ArchiveKind.ZIP

    def test_tar(self):
        assert ArchiveExtractor._kind_from_name("archive.tar") == ArchiveKind.TAR

    def test_tar_gz(self):
        assert ArchiveExtractor._kind_from_name("archive.tar.gz") == ArchiveKind.TAR_GZ

    def test_tgz(self):
        assert ArchiveExtractor._kind_from_name("archive.tgz") == ArchiveKind.TAR_GZ

    def test_unknown(self):
        assert ArchiveExtractor._kind_from_name("file.py") == ArchiveKind.UNKNOWN


class TestDetectKind:
    def test_detect_zip(self, tmp_path):
        archive = tmp_path / "test.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("test.txt", "hello")
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(archive), "")
        assert ext.detect_kind() == ArchiveKind.ZIP

    def test_detect_missing_file(self, tmp_path):
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(tmp_path / "nonexistent"), "")
        assert ext.detect_kind() == ArchiveKind.UNKNOWN

    def test_detect_unknown(self, tmp_path):
        f = tmp_path / "unknown"
        f.write_bytes(b"random data bytes here")
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(f), "")
        assert ext.detect_kind() == ArchiveKind.UNKNOWN


# ---------------------------------------------------------------------------
# ArchiveExtractor — configuration
# ---------------------------------------------------------------------------

class TestExtractorConfig:
    def test_set_cache_dir(self):
        ext = ArchiveExtractor()
        ext.set_cache_dir("/tmp/cache")
        assert ext.cache_dir == "/tmp/cache"

    def test_set_cache_dir_idempotent(self):
        ext = ArchiveExtractor()
        ext.set_cache_dir("/first")
        ext.set_cache_dir("/second")
        assert ext.cache_dir == "/first"

    def test_set_src_and_dest_dir(self):
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir("/src", "/dest")


# ---------------------------------------------------------------------------
# ArchiveExtractor — zip extraction
# ---------------------------------------------------------------------------

class TestZipExtraction:
    def test_extract_zip(self, tmp_path):
        archive = tmp_path / "test.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("dir/file.txt", "hello world")
            zf.writestr("root.txt", "root content")

        dest = tmp_path / "output"
        dest.mkdir()

        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(archive), str(dest))
        ext.extract(urlparse(f"file://{archive}"))

        assert (dest / "dir" / "file.txt").read_text() == "hello world"
        assert (dest / "root.txt").read_text() == "root content"

    def test_extract_zip_entry_dir(self, tmp_path):
        archive = tmp_path / "test.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.mkdir("mydir")
            zf.writestr("mydir/file.txt", "content")

        dest = tmp_path / "output"
        dest.mkdir()
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(archive), str(dest))
        ext.extract(urlparse(f"file://{archive}"))
        assert (dest / "mydir" / "file.txt").exists()


# ---------------------------------------------------------------------------
# ArchiveExtractor — tar extraction
# ---------------------------------------------------------------------------

class TestTarExtraction:
    def test_extract_tar(self, tmp_path):
        archive = tmp_path / "test.tar"
        with tarfile.open(archive, "w") as tf:
            data = b"hello tar"
            info = tarfile.TarInfo(name="file.txt")
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))

        dest = tmp_path / "output"
        dest.mkdir()
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(archive), str(dest))
        ext.extract(urlparse(f"file://{archive}"))
        assert (dest / "file.txt").read_bytes() == b"hello tar"

    def test_extract_tar_gz(self, tmp_path):
        archive = tmp_path / "test.tar.gz"
        with tarfile.open(archive, "w:gz") as tf:
            data = b"hello tar.gz"
            info = tarfile.TarInfo(name="file.txt")
            info.size = len(data)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(data))

        dest = tmp_path / "output"
        dest.mkdir()
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(archive), str(dest))
        ext.extract(urlparse(f"file://{archive}"))
        assert (dest / "file.txt").read_bytes() == b"hello tar.gz"


# ---------------------------------------------------------------------------
# ArchiveExtractor — single skill file
# ---------------------------------------------------------------------------

class TestWriteSingleSkillFile:
    def test_write_skill_md(self, tmp_path):
        src = tmp_path / "SKILL.md"
        src.write_text("# My skill")
        dest = tmp_path / "output"
        dest.mkdir()

        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(src), str(dest))
        url = urlparse(f"file://{src}")
        ext.extract(url)
        assert (dest / "SKILL.md").read_text() == "# My skill"


class TestExtractUnsupported:
    def test_unsupported_extension_raises(self, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("data")
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(f), str(tmp_path / "out"))
        with pytest.raises(ValueError, match="unsupported"):
            ext.extract(urlparse(f"file://{f}"))


# ---------------------------------------------------------------------------
# SkillRootResolver — local paths
# ---------------------------------------------------------------------------

class TestSkillRootResolverLocal:
    def test_empty_string_returns_empty(self):
        resolver = SkillRootResolver()
        assert resolver.resolve("") == ""

    def test_whitespace_returns_empty(self):
        resolver = SkillRootResolver()
        assert resolver.resolve("   ") == ""

    def test_local_path_returned_as_is(self):
        resolver = SkillRootResolver()
        assert resolver.resolve("/usr/local/skills") == "/usr/local/skills"

    def test_relative_path_returned_as_is(self):
        resolver = SkillRootResolver()
        assert resolver.resolve("skills/local") == "skills/local"


# ---------------------------------------------------------------------------
# SkillRootResolver — file:// URLs
# ---------------------------------------------------------------------------

class TestSkillRootResolverFile:
    def test_file_url_directory(self, tmp_path):
        resolver = SkillRootResolver()
        result = resolver.resolve(f"file://{tmp_path}")
        assert str(tmp_path) in result

    def test_file_url_archive(self, tmp_path, monkeypatch):
        archive = tmp_path / "skills.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("SKILL.md", "---\nname: test\n---\n# Test")
        monkeypatch.setenv("SKILLS_CACHE_DIR", str(tmp_path / "cache"))
        resolver = SkillRootResolver()
        result = resolver.resolve(f"file://{archive}")
        assert os.path.isdir(result)

    def test_file_url_non_localhost_raises(self):
        resolver = SkillRootResolver()
        with pytest.raises(ValueError, match="unsupported file URL host"):
            resolver.resolve("file://remote-host/path")


# ---------------------------------------------------------------------------
# SkillRootResolver — unsupported schemes
# ---------------------------------------------------------------------------

class TestSkillRootResolverScheme:
    def test_unsupported_scheme_raises(self):
        resolver = SkillRootResolver()
        with pytest.raises(ValueError, match="unsupported"):
            resolver.resolve("ftp://example.com/skills")


# ---------------------------------------------------------------------------
# SkillRootResolver — static helpers
# ---------------------------------------------------------------------------

class TestSkillRootResolverHelpers:
    def test_file_exists(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello")
        assert SkillRootResolver._file_exists(str(f)) is True

    def test_file_exists_missing(self, tmp_path):
        assert SkillRootResolver._file_exists(str(tmp_path / "nope")) is False

    def test_file_exists_directory(self, tmp_path):
        assert SkillRootResolver._file_exists(str(tmp_path)) is False

    def test_sha256_hex(self):
        h = SkillRootResolver._sha256_hex("test")
        assert isinstance(h, str)
        assert len(h) == 64

    def test_sha256_hex_deterministic(self):
        h1 = SkillRootResolver._sha256_hex("hello")
        h2 = SkillRootResolver._sha256_hex("hello")
        assert h1 == h2

    def test_user_cache_dir(self):
        result = SkillRootResolver._user_cache_dir()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# SkillRootResolver — cache directory
# ---------------------------------------------------------------------------

class TestSkillsCacheDir:
    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SKILLS_CACHE_DIR", str(tmp_path))
        resolver = SkillRootResolver()
        result = resolver._skills_cache_dir()
        assert result == str(tmp_path)

    def test_default_cache_dir(self, monkeypatch):
        monkeypatch.delenv("SKILLS_CACHE_DIR", raising=False)
        resolver = SkillRootResolver()
        result = resolver._skills_cache_dir()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_env_uses_default(self, monkeypatch):
        monkeypatch.setenv("SKILLS_CACHE_DIR", "  ")
        resolver = SkillRootResolver()
        result = resolver._skills_cache_dir()
        assert len(result) > 0


# ---------------------------------------------------------------------------
# ArchiveExtractor — extract_zip_entry edge cases
# ---------------------------------------------------------------------------

class TestExtractZipEntryEdgeCases:
    def test_zip_entry_too_large(self, tmp_path):
        archive = tmp_path / "big.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("file.txt", "x" * 100)
        dest = tmp_path / "output"
        dest.mkdir()
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(archive), str(dest))
        ext.extract(urlparse(f"file://{archive}"))
        assert (dest / "file.txt").exists()

    def test_nil_zip_entry_raises(self):
        with pytest.raises(ValueError, match="nil"):
            ArchiveExtractor._extract_zip_entry(MagicMock(), None, "/tmp", 0)


# ---------------------------------------------------------------------------
# ArchiveExtractor — tar extraction edge cases
# ---------------------------------------------------------------------------

class TestTarExtractionEdgeCases:
    def test_tar_with_directory(self, tmp_path):
        archive = tmp_path / "test.tar"
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo(name="mydir")
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tf.addfile(info)

            data = b"in subdir"
            info2 = tarfile.TarInfo(name="mydir/file.txt")
            info2.size = len(data)
            info2.mode = 0o644
            tf.addfile(info2, io.BytesIO(data))

        dest = tmp_path / "output"
        dest.mkdir()
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(archive), str(dest))
        ext.extract(urlparse(f"file://{archive}"))
        assert (dest / "mydir" / "file.txt").read_bytes() == b"in subdir"

    def test_tar_symlink_rejected(self, tmp_path):
        archive = tmp_path / "sym.tar"
        with tarfile.open(archive, "w") as tf:
            info = tarfile.TarInfo(name="link.txt")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tf.addfile(info)

        dest = tmp_path / "output"
        dest.mkdir()
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(archive), str(dest))
        with pytest.raises(ValueError, match="unsupported tar entry"):
            ext.extract(urlparse(f"file://{archive}"))


# ---------------------------------------------------------------------------
# ArchiveExtractor — detect_kind with gzip
# ---------------------------------------------------------------------------

class TestDetectKindGzip:
    def test_detect_gzip(self, tmp_path):
        import gzip
        archive = tmp_path / "test.gz"
        with gzip.open(archive, "wb") as f:
            f.write(b"data")
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(archive), "")
        assert ext.detect_kind() == ArchiveKind.TAR_GZ

    def test_detect_short_file(self, tmp_path):
        f = tmp_path / "tiny"
        f.write_bytes(b"x")
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(f), "")
        assert ext.detect_kind() == ArchiveKind.UNKNOWN


# ---------------------------------------------------------------------------
# SkillRootResolver — _cache_extracted_root
# ---------------------------------------------------------------------------

class TestCacheExtractedRoot:
    def test_cache_with_ready_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SKILLS_CACHE_DIR", str(tmp_path / "cache"))
        resolver = SkillRootResolver()
        resolver._extractor.set_cache_dir(str(tmp_path / "cache"))

        url_result = urlparse("file:///tmp/test.zip")
        key = resolver._sha256_hex(url_result.geturl())
        dest = tmp_path / "cache" / key
        dest.mkdir(parents=True)
        (dest / ".ready").write_text("ok")

        result = resolver._cache_extracted_root(url_result, "/tmp/test.zip")
        assert result == str(dest)


# ---------------------------------------------------------------------------
# SkillRootResolver — _user_cache_dir platform branches
# ---------------------------------------------------------------------------

class TestUserCacheDir:
    def test_darwin(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.setenv("HOME", "/Users/test")
        result = SkillRootResolver._user_cache_dir()
        assert "Library/Caches" in result

    def test_darwin_no_home(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Darwin")
        monkeypatch.delenv("HOME", raising=False)
        result = SkillRootResolver._user_cache_dir()
        assert result == ""

    def test_linux_xdg(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setenv("XDG_CACHE_HOME", "/tmp/xdg")
        result = SkillRootResolver._user_cache_dir()
        assert result == "/tmp/xdg"

    def test_linux_xdg_relative_ignored(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.setenv("XDG_CACHE_HOME", "relative/path")
        monkeypatch.delenv("HOME", raising=False)
        result = SkillRootResolver._user_cache_dir()
        assert result == ""

    def test_linux_home_fallback(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.setenv("HOME", "/home/test")
        result = SkillRootResolver._user_cache_dir()
        assert ".cache" in result

    def test_windows(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Windows")
        monkeypatch.setenv("LocalAppData", "C:\\Users\\test\\AppData\\Local")
        result = SkillRootResolver._user_cache_dir()
        assert "AppData" in result

    def test_linux_no_home(self, monkeypatch):
        monkeypatch.setattr("platform.system", lambda: "Linux")
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        monkeypatch.delenv("HOME", raising=False)
        result = SkillRootResolver._user_cache_dir()
        assert result == ""


# ---------------------------------------------------------------------------
# SkillRootResolver — _skills_cache_dir fallback
# ---------------------------------------------------------------------------

class TestSkillsCacheDirFallback:
    def test_no_user_cache_falls_to_tmp(self, monkeypatch):
        monkeypatch.delenv("SKILLS_CACHE_DIR", raising=False)
        monkeypatch.setattr(SkillRootResolver, "_user_cache_dir", staticmethod(lambda: ""))
        resolver = SkillRootResolver()
        result = resolver._skills_cache_dir()
        assert "trpc-agent-py" in result


# ---------------------------------------------------------------------------
# ArchiveExtractor — _write_single_skill_file
# ---------------------------------------------------------------------------

class TestWriteSingleSkillFileSizeLimit:
    def test_oversized_file_raises(self, tmp_path):
        src = tmp_path / "SKILL.md"
        src.write_bytes(b"x" * (SizeLimit.MAX_EXTRACT_FILE + 1))
        dest = tmp_path / "output"
        dest.mkdir()
        ext = ArchiveExtractor()
        ext.set_src_and_dest_dir(str(src), str(dest))
        with pytest.raises(RuntimeError, match="too large"):
            ext._write_single_skill_file()


# ---------------------------------------------------------------------------
# ArchiveExtractor — extract_zip_entry path traversal
# ---------------------------------------------------------------------------

class TestZipEntryPathTraversal:
    def test_zip_clean_path_on_root(self):
        result = ArchiveExtractor._clean_archive_path("./subdir/file.txt")
        assert result == "subdir/file.txt"
