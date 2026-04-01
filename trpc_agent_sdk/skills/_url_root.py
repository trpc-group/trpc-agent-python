# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Resolve and cache URL-based skill roots.

This module provides two main classes:

- ``ArchiveExtractor``: detects and extracts zip / tar / tar.gz archives with
  path-traversal protection and configurable size limits.
- ``SkillRootResolver``: resolves a skill-root string (local path, file://, or
  http(s)://) to an absolute local directory, downloading and caching remote
  archives as needed.

Typical usage::

    resolver = SkillRootResolver("https://example.com/skills.tar.gz")
    local_path = resolver.resolve()
"""

import gzip
import hashlib
import io
import os
import platform
import shutil
import tarfile
import tempfile
import zipfile
from enum import Enum
from enum import IntEnum
from urllib.parse import ParseResult
from urllib.parse import urlparse

import requests

from ._constants import ENV_SKILLS_CACHE_DIR
from ._constants import SKILL_FILE

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CacheConfig(str, Enum):
    """File-system names used when caching downloaded skill roots."""

    APP_DIR = "trpc-agent-py"
    SKILLS_DIR = "skills"
    # Sentinel file written inside a cache entry once extraction succeeds.
    READY_FILE = ".ready"
    # Name of the raw downloaded archive inside the temp directory.
    DOWNLOAD_FILE = "download"
    # Sub-directory inside the temp directory where the archive is extracted.
    EXTRACT_DIR = "root"
    # Prefix for temporary working directories so they are easy to identify.
    TEMP_PREFIX = "tmp-skill-root-"


class ArchiveExt(str, Enum):
    """Recognized archive file-name extensions."""

    ZIP = ".zip"
    TAR = ".tar"
    TGZ = ".tgz"
    TAR_GZ = ".tar.gz"


class ArchiveKind(IntEnum):
    """Canonical archive format identifiers."""

    UNKNOWN = 0
    ZIP = 1
    TAR = 2
    TAR_GZ = 3


class FilePerm(IntEnum):
    """POSIX permission modes applied to extracted files and directories."""

    DIR = 0o755  # rwxr-xr-x
    FILE = 0o644  # rw-r--r--


class SizeLimit(IntEnum):
    """Hard limits used to guard against decompression bombs and runaway downloads."""

    BYTES_PER_MIB = 1 << 20
    MAX_DOWNLOAD = 64 << 20  # 64 MiB – maximum raw download size
    MAX_EXTRACT_FILE = 64 << 20  # 64 MiB – maximum size of a single extracted file
    MAX_EXTRACT_TOTAL = 256 << 20  # 256 MiB – maximum aggregate size of all extracted files


class TarPerm(IntEnum):
    """Individual POSIX permission bits as stored in a tar header."""

    USER_READ = 0o400
    USER_WRITE = 0o200
    USER_EXEC = 0o100
    GROUP_READ = 0o040
    GROUP_WRITE = 0o020
    GROUP_EXEC = 0o010
    OTHER_READ = 0o004
    OTHER_WRITE = 0o002
    OTHER_EXEC = 0o001


# ---------------------------------------------------------------------------
# ArchiveExtractor
# ---------------------------------------------------------------------------


class ArchiveExtractor:
    """Detects and extracts skill root archives (zip / tar / tar.gz).

    Before calling :meth:`extract`, configure the extractor with:

    - :meth:`set_cache_dir`   – where the cache lives (set once).
    - :meth:`set_src_and_dest_dir` – source archive path and extraction target.
    """

    def __init__(self) -> None:
        self._cache_dir = ""
        self._src_path = ""
        self._dest_dir = ""

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_cache_dir(self, cache_dir: str) -> None:
        """Set the cache directory once; subsequent calls are no-ops.

        Args:
            cache_dir: Absolute path to the top-level cache directory.
        """
        if self._cache_dir:
            return
        self._cache_dir = cache_dir

    def set_src_and_dest_dir(self, src_path: str, dest_dir: str) -> None:
        """Set the source archive and extraction target for the next :meth:`extract` call.

        Args:
            src_path: Absolute path to the downloaded archive file.
            dest_dir: Absolute path to the directory where contents will be extracted.
        """
        self._src_path = src_path
        self._dest_dir = dest_dir

    @property
    def cache_dir(self) -> str:
        """The configured cache directory (empty string if not yet set)."""
        return self._cache_dir

    # ------------------------------------------------------------------
    # Path / permission helpers (class methods – no instance state needed)
    # ------------------------------------------------------------------

    @classmethod
    def _clean_archive_path(cls, name: str) -> str:
        """Sanitize an archive entry path, rejecting directory-traversal attempts.

        Normalizes separators, resolves ``.`` / ``..`` components, and rejects
        any path that would escape the extraction root.

        Args:
            name: Raw entry name from the archive.

        Returns:
            A clean, relative POSIX-style path, or an empty string for the
            archive root entry itself.

        Raises:
            ValueError: If the path is absolute, contains ``..`` traversal, or
                contains a Windows drive letter colon.
        """
        # Normalize Windows back-slashes before any further processing.
        name = name.replace("\\", "/")
        # Let the OS resolve redundant separators and dot segments.
        name = os.path.normpath(name).replace(os.sep, "/")

        if name == ".":
            # The archive root directory itself – skip silently.
            return ""

        # Reject paths that would write outside the extraction directory.
        if name.startswith("/") or name == ".." or name.startswith("../"):
            raise ValueError(f"invalid archive path: {name!r}")

        # Reject Windows drive letters (e.g. "C:foo") to be platform-safe.
        if ":" in name:
            raise ValueError(f"invalid archive path: {name!r}")

        # Strip a leading "./" that normpath may leave on some platforms.
        if name.startswith("./"):
            name = name[2:]

        return name

    @classmethod
    def _sanitize_perm(cls, mode: int) -> int:
        """Return a safe file permission mode, defaulting to ``FilePerm.FILE`` for zero.

        Args:
            mode: Raw permission bits (typically from an archive header).

        Returns:
            The low 9 permission bits (``0o777`` mask), or ``FilePerm.FILE`` if
            ``mode`` is zero (e.g. the header omitted permissions).
        """
        if mode == 0:
            return FilePerm.FILE
        return mode & 0o777

    @classmethod
    def _tar_header_perm(cls, mode: int) -> int:
        """Extract and sanitize permission bits from a tar header mode word.

        Tar stores the full ``st_mode`` value; we isolate only the nine
        ``rwxrwxrwx`` bits and pass them through :meth:`_sanitize_perm`.

        Args:
            mode: The raw ``mode`` field from a :class:`tarfile.TarInfo` member.

        Returns:
            A sanitized permission value suitable for :func:`os.chmod`.
        """
        if mode < 0:
            # Negative mode is malformed; fall back to a safe default.
            return FilePerm.FILE
        perm = 0
        for bit in TarPerm:
            if mode & bit:
                perm |= bit
        return cls._sanitize_perm(perm)

    @classmethod
    def _add_extracted_bytes(cls, total: int, num: int) -> int:
        """Accumulate the running total of extracted bytes, enforcing the global limit.

        Args:
            total: Bytes extracted so far across all entries.
            num: Bytes about to be added for the current entry.

        Returns:
            Updated running total (``total + num``).

        Raises:
            RuntimeError: If ``num`` is negative or the new total would exceed
                :attr:`SizeLimit.MAX_EXTRACT_TOTAL`.
        """
        if num < 0:
            raise RuntimeError("negative extract size")
        if total > SizeLimit.MAX_EXTRACT_TOTAL - num:
            raise RuntimeError("skills root archive too large")
        return total + num

    @classmethod
    def _validate_tar_size(cls, size: int) -> None:
        """Reject a tar entry whose declared size is invalid or exceeds the per-file limit.

        Args:
            size: The ``size`` field from a :class:`tarfile.TarInfo` member.

        Raises:
            RuntimeError: If ``size`` is negative or exceeds
                :attr:`SizeLimit.MAX_EXTRACT_FILE`.
        """
        if size < 0:
            raise RuntimeError("tar entry has negative size")
        if size > SizeLimit.MAX_EXTRACT_FILE:
            raise RuntimeError("tar entry too large")

    # ------------------------------------------------------------------
    # Archive-kind detection
    # ------------------------------------------------------------------

    @classmethod
    def _kind_from_name(cls, name: str) -> ArchiveKind:
        """Infer the archive format from a lower-cased file name.

        ``TAR_GZ`` takes priority over ``TAR`` because ``.tar.gz`` ends with
        both ``.tar.gz`` and ``.gz``, not ``.tar``.

        Args:
            name: Lower-cased base name of the URL path.

        Returns:
            The matching :class:`ArchiveKind`, or ``UNKNOWN`` if unrecognized.
        """
        if name.endswith(ArchiveExt.ZIP):
            return ArchiveKind.ZIP
        # Check compound extension before the plain .tar suffix.
        if name.endswith(ArchiveExt.TAR_GZ) or name.endswith(ArchiveExt.TGZ):
            return ArchiveKind.TAR_GZ
        if name.endswith(ArchiveExt.TAR):
            return ArchiveKind.TAR
        return ArchiveKind.UNKNOWN

    def detect_kind(self) -> ArchiveKind:
        """Detect the archive format by inspecting magic bytes in the file header.

        Falls back to ``UNKNOWN`` if the file cannot be opened or the header
        does not match a known signature.

        Magic byte references:
        - ZIP:    ``PK\\x03\\x04`` (local file header signature)
        - GZip:   ``\\x1f\\x8b`` (ID1 + ID2 per RFC 1952)

        Returns:
            The detected :class:`ArchiveKind`.
        """
        try:
            with open(self._src_path, "rb") as f:
                hdr = f.read(4)
        except OSError:
            return ArchiveKind.UNKNOWN

        if len(hdr) >= 4 and hdr[:4] == b"PK\x03\x04":
            return ArchiveKind.ZIP
        if len(hdr) >= 2 and hdr[0] == 0x1F and hdr[1] == 0x8B:
            # A gzip-compressed file; assume it wraps a tar archive.
            return ArchiveKind.TAR_GZ
        return ArchiveKind.UNKNOWN

    # ------------------------------------------------------------------
    # Per-entry extraction helpers
    # ------------------------------------------------------------------

    @classmethod
    def _extract_zip_entry(
        cls,
        zr: zipfile.ZipFile,
        f: zipfile.ZipInfo,
        dest_dir: str,
        total: int,
    ) -> int:
        """Extract a single zip entry into ``dest_dir``, enforcing size and path safety.

        The uncompressed size is checked twice:
        1. Against the header-declared ``file_size`` (fast, but can be spoofed).
        2. Against the actual bytes read (guards against zip-bomb header lies).

        Args:
            zr: The open :class:`zipfile.ZipFile` being iterated.
            f: Metadata for the entry to extract.
            dest_dir: Extraction root directory.
            total: Running total of extracted bytes (updated and returned).

        Returns:
            Updated running total after this entry.

        Raises:
            ValueError: If the entry object is ``None``.
            RuntimeError: If the entry exceeds :attr:`SizeLimit.MAX_EXTRACT_FILE` or
                the running total exceeds :attr:`SizeLimit.MAX_EXTRACT_TOTAL`.
        """
        if f is None:
            raise ValueError("nil zip entry")

        clean = cls._clean_archive_path(f.filename)
        if clean == "":
            return total

        target = os.path.join(dest_dir, clean.replace("/", os.sep))

        if f.is_dir():
            os.makedirs(target, mode=FilePerm.DIR, exist_ok=True)
            return total

        # First check: header-declared uncompressed size (cheap, but spoofable).
        if f.file_size > SizeLimit.MAX_EXTRACT_FILE:
            raise RuntimeError(f"zip entry too large: {f.filename!r}")

        os.makedirs(os.path.dirname(target), mode=FilePerm.DIR, exist_ok=True)

        # Determine output file permissions from the Unix external attributes.
        external_attr = f.external_attr >> 16
        mode = cls._sanitize_perm(external_attr & 0o777) if external_attr else FilePerm.FILE

        # Second check: read one extra byte to detect if actual data overflows.
        with zr.open(f) as rc:
            data = rc.read(SizeLimit.MAX_EXTRACT_FILE + 1)
            if len(data) > SizeLimit.MAX_EXTRACT_FILE:
                raise RuntimeError(f"zip entry too large: {f.filename!r}")

        total = cls._add_extracted_bytes(total, len(data))

        with open(target, "wb") as out:
            out.write(data)
        os.chmod(target, mode)

        return total

    @classmethod
    def _extract_tar_reader(cls, fileobj: io.IOBase, dest_dir: str) -> None:
        """Extract all entries from an open tar stream into ``dest_dir``.

        Handles both plain and gzip-compressed streams (the caller is
        responsible for decompression before passing ``fileobj``).  Only
        regular files and directories are accepted; symlinks and hard links
        are rejected to prevent escape-from-sandbox attacks.

        Args:
            fileobj: A readable binary stream positioned at the start of a tar
                archive.  Accepts any :class:`io.IOBase` subclass, including
                :class:`gzip.GzipFile`.
            dest_dir: Extraction root directory.

        Raises:
            RuntimeError: If an entry exceeds size limits or cannot be extracted.
            ValueError: If an entry type is not a regular file or directory.
        """
        total = 0
        with tarfile.open(fileobj=fileobj, mode="r|*") as tr:
            for member in tr:
                clean = cls._clean_archive_path(member.name)
                if clean == "":
                    continue

                target = os.path.join(dest_dir, clean.replace("/", os.sep))

                if member.isdir():
                    os.makedirs(target, mode=FilePerm.DIR, exist_ok=True)
                elif member.isreg():
                    os.makedirs(os.path.dirname(target), mode=FilePerm.DIR, exist_ok=True)
                    cls._validate_tar_size(member.size)
                    total = cls._add_extracted_bytes(total, member.size)
                    mode = cls._tar_header_perm(member.mode)
                    extracted = tr.extractfile(member)
                    if extracted is None:
                        raise RuntimeError(f"cannot extract tar entry: {member.name!r}")
                    with open(target, "wb") as out:
                        out.write(extracted.read(member.size))
                    os.chmod(target, mode)
                else:
                    # Reject symlinks, hard links, device nodes, etc. to prevent
                    # path-traversal attacks via symlink chains.
                    raise ValueError(f"unsupported tar entry type: {member.name!r}")

    # ------------------------------------------------------------------
    # Format-specific extraction entry points
    # ------------------------------------------------------------------

    def _extract_zip(self) -> None:
        """Extract the configured zip archive to the destination directory."""
        with zipfile.ZipFile(self._src_path, "r") as zr:
            total = 0
            for f in zr.infolist():
                total = self._extract_zip_entry(zr, f, self._dest_dir, total)

    def _extract_tar(self) -> None:
        """Extract the configured plain tar archive to the destination directory."""
        with open(self._src_path, "rb") as f:
            self._extract_tar_reader(f, self._dest_dir)

    def _extract_tar_gz(self) -> None:
        """Decompress and extract the configured gzip-compressed tar archive."""
        with open(self._src_path, "rb") as f:
            with gzip.open(f) as gz:
                self._extract_tar_reader(gz, self._dest_dir)

    def _write_single_skill_file(self) -> None:
        """Copy a single bare skill file (``SKILL.md``) into the destination directory.

        This handles the edge case where the URL points directly to a
        ``SKILL.md`` file rather than an archive containing one.

        Raises:
            RuntimeError: If the source file exceeds :attr:`SizeLimit.MAX_EXTRACT_FILE`.
        """
        st = os.stat(self._src_path)
        if st.st_size > SizeLimit.MAX_EXTRACT_FILE:
            raise RuntimeError("skill file too large")
        with open(self._src_path, "rb") as f:
            data = f.read()
        dest = os.path.join(self._dest_dir, SKILL_FILE)
        with open(dest, "wb") as f:
            f.write(data)
        os.chmod(dest, FilePerm.FILE)

    # ------------------------------------------------------------------
    # Public extraction entry point
    # ------------------------------------------------------------------

    def extract(self, url_result: ParseResult) -> None:
        """Detect the archive format and extract it into the destination directory.

        Format detection order:
        1. File name extension from the URL path.
        2. Magic bytes from the downloaded file (fallback when the URL has no
           recognizable extension).
        3. Bare ``SKILL.md`` file (last resort for direct-file URLs).

        Args:
            url_result: Parsed URL whose ``path`` component supplies the file name
                used for extension-based detection.

        Raises:
            ValueError: If the archive format cannot be determined or is unsupported.
        """
        name = os.path.basename(url_result.path).lower()
        kind = self._kind_from_name(name)
        if kind == ArchiveKind.UNKNOWN:
            # Extension was not recognized; fall back to magic-byte sniffing.
            kind = self.detect_kind()

        if kind == ArchiveKind.ZIP:
            self._extract_zip()
        elif kind == ArchiveKind.TAR:
            self._extract_tar()
        elif kind == ArchiveKind.TAR_GZ:
            self._extract_tar_gz()
        else:
            # Last-resort: a plain SKILL.md file served directly via URL.
            if name == SKILL_FILE.lower():
                self._write_single_skill_file()
            else:
                raise ValueError(f"unsupported skills root file: {url_result.path}")


# ---------------------------------------------------------------------------
# SkillRootResolver
# ---------------------------------------------------------------------------


class SkillRootResolver:
    """Resolves a skill-root string to an absolute local directory path.

    Supported root formats:

    - **Local path** – returned as-is after stripping whitespace.
    - **``file://``** – local directories/plain files are returned directly;
      local archives are extracted into cache and resolved to that cache path.
    - **``http://`` / ``https://``** – the archive is downloaded once, extracted
      under the user-level cache directory, and the cache path is returned on
      subsequent calls.

    Args:
        root: The raw skill-root string supplied by the caller.
    """

    def __init__(self, root: str) -> None:
        self._root = root
        self._extractor = ArchiveExtractor()

    # ------------------------------------------------------------------
    # Static helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _file_exists(path: str) -> bool:
        """Return ``True`` if ``path`` exists and is a regular file (not a directory).

        Uses :func:`os.stat` rather than :func:`os.path.exists` to avoid a
        separate ``isfile`` call, reducing the number of syscalls.

        Args:
            path: Filesystem path to check.
        """
        try:
            os.stat(path)
            return not os.path.isdir(path)
        except OSError:
            return False

    @staticmethod
    def _sha256_hex(s: str) -> str:
        """Return the hex-encoded SHA-256 digest of a UTF-8 string.

        Used to derive a stable, filesystem-safe cache directory name from a URL.

        Args:
            s: The string to hash (typically a canonical URL).
        """
        return hashlib.sha256(s.encode("utf-8")).hexdigest()

    @staticmethod
    def _user_cache_dir() -> str:
        """Return the platform-appropriate user cache directory.

        Resolution order:

        - **macOS / iOS**: ``$HOME/Library/Caches``
        - **Windows**: ``%LocalAppData%``
        - **Other (Linux, etc.)**:
          1. ``$XDG_CACHE_HOME`` if set *and* absolute (per XDG Base Directory
             Specification §3 – the value must be absolute).
          2. ``$HOME/.cache`` otherwise.

        Returns:
            An absolute path string, or an empty string if no suitable directory
            can be determined.
        """
        system = platform.system().lower()

        if system in {"darwin", "ios"}:
            home = os.getenv("HOME", "").strip()
            if not home:
                return ""
            return os.path.join(home, "Library", "Caches")

        if system == "windows":
            return os.getenv("LocalAppData", "").strip()

        # Linux / other Unix: honour XDG Base Directory Specification.
        xdg = os.getenv("XDG_CACHE_HOME", "").strip()
        if xdg:
            # Per the XDG spec the value MUST be absolute; ignore it if not.
            if not os.path.isabs(xdg):
                return ""
            return xdg

        home = os.getenv("HOME", "").strip()
        if not home:
            return ""
        return os.path.join(home, ".cache")

    # ------------------------------------------------------------------
    # Cache directory resolution
    # ------------------------------------------------------------------

    def _skills_cache_dir(self) -> str:
        """Return the directory used to cache downloaded skill roots.

        The environment variable :data:`ENV_SKILLS_CACHE_DIR` overrides the
        default location.  When the variable is unset, the cache is placed
        under the platform user-cache directory; if that cannot be determined
        (e.g. no ``$HOME``), the system temp directory is used instead.

        Returns:
            An absolute path to the skills cache directory.
        """
        override = os.environ.get(ENV_SKILLS_CACHE_DIR, "").strip()
        if override:
            return override

        uc = self._user_cache_dir()
        if uc:
            return os.path.join(uc, CacheConfig.APP_DIR, CacheConfig.SKILLS_DIR)
        # Fall back to the system temp directory when no user cache is available.
        return os.path.join(tempfile.gettempdir(), CacheConfig.APP_DIR, CacheConfig.SKILLS_DIR)

    # ------------------------------------------------------------------
    # URL-specific resolvers
    # ------------------------------------------------------------------

    def _cache_extracted_root(self, url_result: ParseResult, src_path: str) -> str:
        """Extract ``src_path`` and cache the populated skill root directory.

        Args:
            url_result: Parsed source URL used for cache keying and format hints.
            src_path: Local path to the archive (or bare ``SKILL.md`` file).

        Returns:
            Absolute path to the populated cache directory.
        """
        key = self._sha256_hex(url_result.geturl())
        dest_dir = os.path.join(self._extractor.cache_dir, key)
        ready = os.path.join(dest_dir, CacheConfig.READY_FILE)

        if self._file_exists(ready):
            return dest_dir

        if os.path.exists(dest_dir):
            shutil.rmtree(dest_dir, ignore_errors=True)

        os.makedirs(self._extractor.cache_dir, mode=FilePerm.DIR, exist_ok=True)

        tmp_dir = tempfile.mkdtemp(prefix=CacheConfig.TEMP_PREFIX, dir=self._extractor.cache_dir)
        try:
            extract_dir = os.path.join(tmp_dir, CacheConfig.EXTRACT_DIR)
            os.makedirs(extract_dir, mode=FilePerm.DIR, exist_ok=True)

            self._extractor.set_src_and_dest_dir(src_path, extract_dir)
            self._extractor.extract(url_result)

            ready_path = os.path.join(extract_dir, CacheConfig.READY_FILE)
            with open(ready_path, "w") as f:
                f.write("ok")
            os.chmod(ready_path, FilePerm.FILE)

            try:
                os.rename(extract_dir, dest_dir)
            except OSError:
                if self._file_exists(ready):
                    return dest_dir
                raise
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        return dest_dir

    def _file_url_path(self, url_result: ParseResult) -> str:
        """Extract the local filesystem path from a ``file://`` URL.

        Only ``localhost`` (or an empty host) is accepted; remote file URLs are
        rejected.  On Windows, a leading path separator before a drive letter
        (e.g. ``/C:/Users``) is stripped.

        If the path points to a local archive file, extract it into cache and
        return the cache path, consistent with remote ``http(s)`` archive handling.

        Args:
            url_result: A parsed ``file://`` URL.

        Returns:
            The absolute local filesystem path.

        Raises:
            ValueError: If the URL specifies a non-localhost host.
        """
        if url_result.hostname and url_result.hostname != "localhost":
            raise ValueError(f"unsupported file URL host: {url_result.hostname!r}")

        path = url_result.path

        if os.sep != "/":
            # Convert POSIX separators to the platform-native separator.
            path = path.replace("/", os.sep)

        # On Windows, ``urlparse`` produces a leading separator before the drive
        # letter (e.g. ``\C:\Users``).  Strip it to get a valid path.
        if platform.system() == "Windows" and len(path) > 2 and path[0] == os.sep and path[2] == ":":
            path = path[1:]

        if not os.path.isfile(path):
            return path

        kind = ArchiveExtractor._kind_from_name(os.path.basename(url_result.path).lower())
        if kind == ArchiveKind.UNKNOWN:
            self._extractor.set_src_and_dest_dir(path, "")
            kind = self._extractor.detect_kind()

        if kind != ArchiveKind.UNKNOWN:
            return self._cache_extracted_root(url_result, path)

        return path

    def _download_url_to_file(self, url_result: ParseResult, path: str) -> None:
        """Stream a URL response to a local file, enforcing download size limits.

        The ``Content-Length`` header (when present) is checked before any data
        is written; the running byte count is also checked during streaming to
        handle servers that omit or lie about the header.

        Args:
            url_result: Parsed URL to download.
            path: Destination file path for the raw download.

        Raises:
            RuntimeError: If the server returns a non-2xx status, or if the
                response body exceeds :attr:`SizeLimit.MAX_DOWNLOAD`.
        """
        resp = requests.get(url_result.geturl(), stream=True)
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(f"download skills root: {resp.status_code} {resp.reason}")

        # Eagerly reject based on Content-Length to avoid allocating the buffer.
        content_length = resp.headers.get("Content-Length")
        if content_length is not None and int(content_length) > SizeLimit.MAX_DOWNLOAD:
            raise RuntimeError("download skills root: too large")

        with open(path, "wb") as f:
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                total += len(chunk)
                if total > SizeLimit.MAX_DOWNLOAD:
                    raise RuntimeError("download skills root: too large")
                f.write(chunk)

    def _cache_url(self, url_result: ParseResult) -> str:
        """Download, extract, and cache a remote skill-root archive.

        The cache entry is keyed by the SHA-256 of the canonical URL.  A
        ``.ready`` sentinel file is written as the last step of a successful
        extraction, so a partially-extracted entry (e.g. from a previous crash)
        is automatically cleaned up and retried.

        Concurrent writers are handled via :func:`os.rename`: only the first
        process to rename the temp directory wins; others detect the sentinel
        file and return immediately.

        Args:
            url_result: Parsed ``http`` or ``https`` URL.

        Returns:
            Absolute path to the populated cache directory.
        """
        tmp_dir = tempfile.mkdtemp(prefix=CacheConfig.TEMP_PREFIX, dir=self._extractor.cache_dir)
        try:
            src_path = os.path.join(tmp_dir, CacheConfig.DOWNLOAD_FILE)
            self._download_url_to_file(url_result, src_path)
            return self._cache_extracted_root(url_result, src_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def resolve(self) -> str:
        """Resolve the skill root to an absolute local directory path.

        - Empty / whitespace-only strings return ``""`` immediately.
        - Strings without ``://`` are treated as local paths and returned as-is.
        - ``file://`` URLs resolve to the local path for directories/plain files,
          or to an extracted cache path for archive files.
        - ``http://`` / ``https://`` URLs are downloaded, extracted, and cached;
          the cache path is returned.

        Returns:
            An absolute local path, or an empty string for empty input.

        Raises:
            ValueError: If the URL scheme is not supported.
        """
        root = self._root.strip()
        if not root:
            return ""

        # Fast path: no scheme separator means it is already a local path.
        if "://" not in root:
            return root

        self._extractor.set_cache_dir(self._skills_cache_dir())
        url_result = urlparse(root)

        if url_result.scheme in ("http", "https"):
            return self._cache_url(url_result)

        if url_result.scheme == "file":
            return self._file_url_path(url_result)

        raise ValueError(f"unsupported skills root URL: {root!r}")
