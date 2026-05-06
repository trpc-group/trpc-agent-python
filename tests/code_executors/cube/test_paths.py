# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.code_executors.cube._paths."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.code_executors.cube import _paths
from trpc_agent_sdk.code_executors.cube._paths import (
    join_remote,
    normalize_remote_relative,
    shell_quote,
    wrap_stdin_heredoc,
)


# ---------------------------------------------------------------------------
# shell_quote
# ---------------------------------------------------------------------------


class TestShellQuote:

    def test_empty_string_is_empty_pair(self):
        assert shell_quote("") == "''"

    def test_plain_string_is_wrapped(self):
        assert shell_quote("foo") == "'foo'"

    def test_single_quote_is_escaped(self):
        # foo'bar -> 'foo'\''bar'
        assert shell_quote("foo'bar") == "'foo'\\''bar'"

    def test_spaces_preserved_inside_quotes(self):
        assert shell_quote("a b c") == "'a b c'"

    def test_dollar_backtick_and_glob_are_literal(self):
        quoted = shell_quote("$x `y` * | & ; < >")
        assert quoted == "'$x `y` * | & ; < >'"

    def test_double_quote_untouched(self):
        # Single-quoting intentionally keeps double quotes literal.
        assert shell_quote('she said "hi"') == "'she said \"hi\"'"

    def test_unicode_preserved(self):
        assert shell_quote("café🚀") == "'café🚀'"

    def test_newlines_preserved_inside_quotes(self):
        # Heredoc-wrapped payloads depend on \n surviving quoting.
        assert shell_quote("a\nb") == "'a\nb'"

    @pytest.mark.parametrize("raw", [
        "foo",
        "it's",
        "a b",
        "'",
        "''",
        "'''",
        r"\slash",
        "$HOME",
    ])
    def test_bash_roundtrip(self, raw):
        """Bash must interpret ``shell_quote(s)`` back to ``s`` verbatim.

        The implementation's escape style differs from :func:`shlex.quote`
        (we use ``'\\''`` splicing, shlex uses ``'"'"'``) but both must
        be round-trip safe in a real POSIX shell. Drive this through
        ``bash -c 'printf %s <quoted>'`` and assert the output equals
        the original.
        """
        import subprocess
        quoted = shell_quote(raw)
        out = subprocess.check_output(
            ["bash", "-c", f"printf %s {quoted}"],
        )
        assert out == raw.encode("utf-8")


# ---------------------------------------------------------------------------
# normalize_remote_relative
# ---------------------------------------------------------------------------


class TestNormalizeRemoteRelative:

    def test_empty_string_rejects(self):
        with pytest.raises(ValueError, match="must not be empty"):
            normalize_remote_relative("")

    def test_whitespace_only_rejects(self):
        with pytest.raises(ValueError, match="must not be empty"):
            normalize_remote_relative("   ")

    def test_empty_string_allow_current(self):
        assert normalize_remote_relative("", allow_current=True) == ""

    def test_whitespace_allow_current(self):
        assert normalize_remote_relative("   ", allow_current=True) == ""

    def test_dot_rejects(self):
        with pytest.raises(ValueError, match="must not be empty"):
            normalize_remote_relative(".")

    def test_dot_allow_current(self):
        assert normalize_remote_relative(".", allow_current=True) == ""

    def test_simple_path(self):
        assert normalize_remote_relative("foo/bar") == "foo/bar"

    def test_normalized_path(self):
        assert normalize_remote_relative("./foo/./bar") == "foo/bar"

    def test_collapses_internal_dotdot(self):
        # foo/../bar collapses to bar and stays in-root.
        assert normalize_remote_relative("foo/../bar") == "bar"

    def test_backslashes_converted_to_slashes(self):
        # Windows-style separators converted to posix.
        assert normalize_remote_relative("foo\\bar") == "foo/bar"

    def test_absolute_path_rejected(self):
        with pytest.raises(ValueError, match="escapes its root"):
            normalize_remote_relative("/etc/passwd")

    def test_dotdot_rejected(self):
        with pytest.raises(ValueError, match="escapes its root"):
            normalize_remote_relative("..")

    def test_dotdot_prefix_rejected(self):
        with pytest.raises(ValueError, match="escapes its root"):
            normalize_remote_relative("../etc/passwd")

    def test_internal_escape_via_dotdot_rejected(self):
        # ``foo/../..`` collapses to ``..``; must be rejected.
        with pytest.raises(ValueError, match="escapes its root"):
            normalize_remote_relative("foo/../..")

    def test_strips_whitespace_before_normalizing(self):
        assert normalize_remote_relative("  foo/bar  ") == "foo/bar"


# ---------------------------------------------------------------------------
# join_remote
# ---------------------------------------------------------------------------


class TestJoinRemote:

    def test_empty_relative_returns_root(self):
        assert join_remote("/a/b", "") == "/a/b"

    def test_basic_join(self):
        assert join_remote("/a/b", "c/d") == "/a/b/c/d"

    def test_collapses_dotdot(self):
        assert join_remote("/a/b", "../c") == "/a/c"

    def test_preserves_absolute_root(self):
        assert join_remote("/ws", "subdir/file.txt") == "/ws/subdir/file.txt"


# ---------------------------------------------------------------------------
# wrap_stdin_heredoc
# ---------------------------------------------------------------------------


class TestWrapStdinHeredoc:

    def test_basic_structure(self):
        out = wrap_stdin_heredoc("python3", b"print('hi')")
        lines = out.split("\n")
        # First line: <cmd> << 'MARKER'
        assert lines[0].startswith("python3 << 'TRPC_STDIN_EOF_")
        assert lines[0].endswith("'")
        # Middle line: payload
        assert lines[1] == "print('hi')"
        # Last line: closing marker (no quotes)
        assert lines[2].startswith("TRPC_STDIN_EOF_")

    def test_marker_prefix_is_stable(self):
        out = wrap_stdin_heredoc("cmd", b"body")
        assert "TRPC_STDIN_EOF_" in out

    def test_closing_marker_matches_opening(self):
        out = wrap_stdin_heredoc("cmd", b"body")
        opening_line, body, closing_line = out.split("\n")
        # Extract marker between "<< '" and "'".
        start = opening_line.index("'") + 1
        end = opening_line.rindex("'")
        marker = opening_line[start:end]
        assert closing_line == marker

    def test_utf8_payload_preserved(self):
        out = wrap_stdin_heredoc("cmd", "café🚀".encode("utf-8"))
        assert "café🚀" in out

    def test_binary_payload_routed_through_base64(self):
        # Non-UTF-8 input must NOT be silently lossily decoded. It is
        # routed through ``base64 -d | cmd`` so the original bytes
        # reach the command's stdin verbatim.
        out = wrap_stdin_heredoc("cat", b"\xff\xfe\x00")
        first_line = out.split("\n", 1)[0]
        assert first_line.startswith("base64 -d << 'TRPC_STDIN_EOF_")
        assert first_line.endswith("' | cat")
        # Replacement chars (U+FFFD) must NOT appear anywhere in the
        # rendered command — the whole point of the binary path is
        # lossless transport.
        assert "\ufffd" not in out

    def test_binary_payload_byte_perfect_roundtrip_through_bash(self):
        """End-to-end: rendered command must hand binary bytes to stdin verbatim."""
        import subprocess
        import tempfile
        from pathlib import Path

        payload = bytes(range(256))  # every byte 0x00..0xff
        with tempfile.TemporaryDirectory() as tmp:
            sink = Path(tmp) / "received.bin"
            cmd = wrap_stdin_heredoc(f"cat > {sink}", payload)
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True,
                check=True,
            )
            assert result.returncode == 0
            assert sink.read_bytes() == payload

    def test_utf8_payload_uses_text_fast_path(self):
        # Valid UTF-8 must keep the simple `cmd << 'MARKER'` form so
        # logs remain readable and no subprocess overhead is added.
        out = wrap_stdin_heredoc("python3", "café🚀".encode("utf-8"))
        assert out.startswith("python3 << 'TRPC_STDIN_EOF_")
        assert "base64 -d" not in out

    def test_multiline_payload_preserved(self):
        payload = b"line1\nline2\nline3"
        out = wrap_stdin_heredoc("cmd", payload)
        # Payload is placed between opening and closing marker lines.
        # So middle content is exactly 3 lines.
        sections = out.split("\n")
        # Opening, line1, line2, line3, closing = 5 elements.
        assert len(sections) == 5
        assert sections[1:4] == ["line1", "line2", "line3"]

    def test_marker_collision_is_resolved(self, monkeypatch):
        """Regression guard for the `while marker in payload` retry.

        Feed ``secrets.token_hex`` a first hex that appears in the
        payload and a second distinct hex. The final marker must be the
        non-colliding one.
        """
        colliding_hex = "aaaaaaaaaaaaaaaa"
        safe_hex = "bbbbbbbbbbbbbbbb"
        calls = {"n": 0}

        def fake_token_hex(nbytes):
            calls["n"] += 1
            return colliding_hex if calls["n"] == 1 else safe_hex

        monkeypatch.setattr(_paths.secrets, "token_hex", fake_token_hex)

        payload = b"here is TRPC_STDIN_EOF_" + colliding_hex.encode() + b" inside payload"
        out = wrap_stdin_heredoc("cmd", payload)
        # The chosen marker must be the non-colliding one.
        assert f"TRPC_STDIN_EOF_{safe_hex}" in out
        # Exactly the colliding hex must NOT be the final marker
        opening_line = out.split("\n", 1)[0]
        assert colliding_hex not in opening_line or safe_hex in opening_line
        # Collision detection must have actually consumed the first candidate.
        assert calls["n"] >= 2

    def test_binary_marker_collision_with_command_is_resolved(self, monkeypatch):
        """Regression guard for the binary path's command-collision retry.

        On the base64 branch the *payload* can never collide (base64
        alphabet excludes ``_``), but the *wrapper command* can — e.g. a
        multi-line shell function whose body happens to contain the
        chosen literal. Force the first hex to appear in ``command``
        and verify the second, non-colliding hex wins.
        """
        colliding_hex = "cccccccccccccccc"
        safe_hex = "dddddddddddddddd"
        calls = {"n": 0}

        def fake_token_hex(nbytes):
            calls["n"] += 1
            return colliding_hex if calls["n"] == 1 else safe_hex

        monkeypatch.setattr(_paths.secrets, "token_hex", fake_token_hex)

        # Non-UTF-8 payload routes through _wrap_binary_stdin_heredoc.
        payload = b"\xff\xfe\x00\x80"
        # Embed the colliding marker inside the command itself.
        command = f"cat # TRPC_STDIN_EOF_{colliding_hex} sentinel"
        out = wrap_stdin_heredoc(command, payload)
        # Must use the safe hex on the binary path.
        assert f"base64 -d << 'TRPC_STDIN_EOF_{safe_hex}'" in out
        assert calls["n"] >= 2
