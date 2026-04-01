# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import pytest
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors.utils import build_block_spec
from trpc_agent_sdk.code_executors.utils import normalize_globs


class TestNormalizeGlobs:
    """Test suite for normalize_globs function."""

    def test_normalize_globs_output_dir(self):
        """Test normalizing OUTPUT_DIR variable."""
        patterns = ["$OUTPUT_DIR/a.txt"]
        result = normalize_globs(patterns)
        assert result == ["out/a.txt"]

    def test_normalize_globs_work_dir(self):
        """Test normalizing WORK_DIR variable."""
        patterns = ["$WORK_DIR/x/**"]
        result = normalize_globs(patterns)
        assert result == ["work/x/**"]

    def test_normalize_globs_workspace_dir(self):
        """Test normalizing WORKSPACE_DIR variable."""
        patterns = ["$WORKSPACE_DIR/out"]
        result = normalize_globs(patterns)
        assert result == ["out"]

    def test_normalize_globs_skills_dir(self):
        """Test normalizing SKILLS_DIR variable."""
        patterns = ["$SKILLS_DIR/file.py"]
        result = normalize_globs(patterns)
        assert result == ["skills/file.py"]

    def test_normalize_globs_brace_syntax(self):
        """Test normalizing with brace syntax."""
        patterns = ["${WORK_DIR}/x/**"]
        result = normalize_globs(patterns)
        assert result == ["work/x/**"]

    def test_normalize_globs_multiple_patterns(self):
        """Test normalizing multiple patterns."""
        patterns = ["$OUTPUT_DIR/*.txt", "$WORK_DIR/*.py"]
        result = normalize_globs(patterns)
        assert result == ["out/*.txt", "work/*.py"]

    def test_normalize_globs_no_variables(self):
        """Test normalizing patterns without variables."""
        patterns = ["out/*.txt", "work/*.py"]
        result = normalize_globs(patterns)
        assert result == ["out/*.txt", "work/*.py"]

    def test_normalize_globs_empty(self):
        """Test normalizing empty list."""
        result = normalize_globs([])
        assert result == []

    def test_normalize_globs_whitespace(self):
        """Test normalizing patterns with whitespace."""
        patterns = ["  $OUTPUT_DIR/a.txt  ", "  $WORK_DIR/b.txt  "]
        result = normalize_globs(patterns)
        assert result == ["out/a.txt", "work/b.txt"]

    def test_normalize_globs_empty_strings(self):
        """Test normalizing patterns with empty strings."""
        patterns = ["$OUTPUT_DIR/a.txt", "", "  ", "$WORK_DIR/b.txt"]
        result = normalize_globs(patterns)
        assert len(result) == 2
        assert "out/a.txt" in result
        assert "work/b.txt" in result


class TestBuildBlockSpec:
    """Test suite for build_block_spec function."""

    def test_build_block_spec_python(self):
        """Test building spec for Python block."""
        block = CodeBlock(language="python", code="print('hello')")
        filename, mode, command, args = build_block_spec(0, block)

        assert filename == "code_0.py"
        assert mode == 0o644
        assert command == "python3"
        assert args is None

    def test_build_block_spec_python3(self):
        """Test building spec for Python3 block."""
        block = CodeBlock(language="python3", code="print('hello')")
        filename, mode, command, args = build_block_spec(1, block)

        assert filename == "code_1.py"
        assert command == "python3"

    def test_build_block_spec_py(self):
        """Test building spec for py block."""
        block = CodeBlock(language="py", code="print('hello')")
        filename, mode, command, args = build_block_spec(2, block)

        assert filename == "code_2.py"
        assert command == "python3"

    def test_build_block_spec_bash(self):
        """Test building spec for Bash block."""
        block = CodeBlock(language="bash", code="echo hello")
        filename, mode, command, args = build_block_spec(0, block)

        assert filename == "code_0.sh"
        assert mode == 0o755
        assert command == "bash"
        assert args is None

    def test_build_block_spec_sh(self):
        """Test building spec for sh block."""
        block = CodeBlock(language="sh", code="echo hello")
        filename, mode, command, args = build_block_spec(1, block)

        assert filename == "code_1.sh"
        assert command == "bash"

    def test_build_block_spec_case_insensitive(self):
        """Test building spec is case insensitive."""
        block = CodeBlock(language="PYTHON", code="print('hello')")
        filename, mode, command, args = build_block_spec(0, block)

        assert filename == "code_0.py"
        assert command == "python3"

    def test_build_block_spec_whitespace(self):
        """Test building spec handles whitespace."""
        block = CodeBlock(language="  python  ", code="print('hello')")
        filename, mode, command, args = build_block_spec(0, block)

        assert filename == "code_0.py"
        assert command == "python3"

    def test_build_block_spec_empty_language(self):
        """Test building spec with empty language raises ValueError."""
        block = CodeBlock(language="", code="print('hello')")

        with pytest.raises(ValueError, match="unsupported language"):
            build_block_spec(0, block)

    def test_build_block_spec_unsupported_language(self):
        """Test building spec with unsupported language raises ValueError."""
        block = CodeBlock(language="javascript", code="console.log('hello')")

        with pytest.raises(ValueError, match="unsupported language"):
            build_block_spec(0, block)

    def test_build_block_spec_none_language(self):
        """Test building spec with None language raises ValueError."""
        block = CodeBlock(language="", code="print('hello')")

        with pytest.raises(ValueError, match="unsupported language"):
            build_block_spec(0, block)
