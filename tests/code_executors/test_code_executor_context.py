# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from trpc_agent_sdk.code_executors._code_executor_context import CodeExecutorContext
from trpc_agent_sdk.code_executors._types import CodeBlock
from trpc_agent_sdk.code_executors._types import CodeExecutionResult
from trpc_agent_sdk.code_executors._types import CodeFile
from trpc_agent_sdk.types import Outcome


class TestCodeExecutorContext:
    """Test suite for CodeExecutorContext class."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        self.session_state = {}
        self.context = CodeExecutorContext(self.session_state)

    def test_init_creates_code_execution_state(self):
        """Test that initialization creates code execution state."""
        session_state = {}
        context = CodeExecutorContext(session_state)

        assert "code_execution" in session_state
        assert "input_files" in session_state["code_execution"]
        assert "processed_file_names" in session_state["code_execution"]
        assert "execution_id" in session_state["code_execution"]
        assert "error_counts" in session_state["code_execution"]
        assert "code_execution_results" in session_state["code_execution"]

    def test_init_with_existing_state(self):
        """Test initialization with existing code execution state."""
        session_state = {
            "code_execution": {
                "input_files": [{
                    "name": "test.txt"
                }],
                "processed_file_names": ["test.txt"],
                "execution_id": "exec-123",
                "error_counts": {
                    "inv-1": 2
                },
                "code_execution_results": {},
            }
        }
        context = CodeExecutorContext(session_state)

        assert len(context.get_input_files()) == 1
        assert context.get_execution_id() == "exec-123"

    def test_get_input_files_empty(self):
        """Test getting input files when empty."""
        files = self.context.get_input_files()

        assert files == []

    def test_add_input_files(self):
        """Test adding input files."""
        file1 = CodeFile(name="file1.txt", content="content1", mime_type="text/plain")
        file2 = CodeFile(name="file2.txt", content="content2", mime_type="text/plain")

        self.context.add_input_files([file1, file2])
        files = self.context.get_input_files()

        assert len(files) == 2
        assert files[0].name == "file1.txt"
        assert files[1].name == "file2.txt"

    def test_add_input_files_multiple_times(self):
        """Test adding input files multiple times accumulates."""
        file1 = CodeFile(name="file1.txt", content="content1", mime_type="text/plain")
        file2 = CodeFile(name="file2.txt", content="content2", mime_type="text/plain")

        self.context.add_input_files([file1])
        self.context.add_input_files([file2])
        files = self.context.get_input_files()

        assert len(files) == 2

    def test_get_processed_file_names_empty(self):
        """Test getting processed file names when empty."""
        names = self.context.get_processed_file_names()

        assert names == []

    def test_add_processed_file_names(self):
        """Test adding processed file names."""
        self.context.add_processed_file_names(["file1.txt", "file2.txt"])
        names = self.context.get_processed_file_names()

        assert len(names) == 2
        assert "file1.txt" in names
        assert "file2.txt" in names

    def test_add_processed_file_names_multiple_times(self):
        """Test adding processed file names multiple times accumulates."""
        self.context.add_processed_file_names(["file1.txt"])
        self.context.add_processed_file_names(["file2.txt"])
        names = self.context.get_processed_file_names()

        assert len(names) == 2

    def test_get_execution_id_none(self):
        """Test getting execution ID when not set."""
        execution_id = self.context.get_execution_id()

        assert execution_id is None

    def test_set_and_get_execution_id(self):
        """Test setting and getting execution ID."""
        self.context.set_execution_id("exec-123")
        execution_id = self.context.get_execution_id()

        assert execution_id == "exec-123"

    def test_get_error_count_default_zero(self):
        """Test getting error count for invocation that doesn't exist."""
        count = self.context.get_error_count("inv-1")

        assert count == 0

    def test_increment_error_count(self):
        """Test incrementing error count."""
        self.context.increment_error_count("inv-1")
        count = self.context.get_error_count("inv-1")

        assert count == 1

    def test_increment_error_count_multiple_times(self):
        """Test incrementing error count multiple times."""
        self.context.increment_error_count("inv-1")
        self.context.increment_error_count("inv-1")
        self.context.increment_error_count("inv-1")
        count = self.context.get_error_count("inv-1")

        assert count == 3

    def test_increment_error_count_different_invocations(self):
        """Test incrementing error count for different invocations."""
        self.context.increment_error_count("inv-1")
        self.context.increment_error_count("inv-2")
        self.context.increment_error_count("inv-1")

        assert self.context.get_error_count("inv-1") == 2
        assert self.context.get_error_count("inv-2") == 1

    def test_reset_error_count(self):
        """Test resetting error count."""
        self.context.increment_error_count("inv-1")
        self.context.increment_error_count("inv-1")
        self.context.reset_error_count("inv-1")
        count = self.context.get_error_count("inv-1")

        assert count == 0

    def test_reset_error_count_not_existing(self):
        """Test resetting error count for non-existing invocation."""
        # Should not raise error
        self.context.reset_error_count("inv-nonexistent")
        count = self.context.get_error_count("inv-nonexistent")

        assert count == 0

    def test_update_code_execution_result(self):
        """Test updating code execution result."""
        code_blocks = [
            CodeBlock(language="python", code="print('hello')"),
            CodeBlock(language="python", code="print('world')"),
        ]
        result = CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="hello\nworld")
        self.context.update_code_execution_result("inv-1", code_blocks, result)

        results = self.session_state["code_execution"]["code_execution_results"]
        assert "inv-1" in results
        assert len(results["inv-1"]) == 1
        assert results["inv-1"][0]["code"] == "print('hello')\nprint('world')"
        assert results["inv-1"][0]["result"]["outcome"] == Outcome.OUTCOME_OK
        assert results["inv-1"][0]["result"]["output"] == 'hello\nworld'

    def test_update_code_execution_result_multiple_times(self):
        """Test updating code execution result multiple times."""
        code_blocks1 = [CodeBlock(language="python", code="print('first')")]
        code_blocks2 = [CodeBlock(language="python", code="print('second')")]

        result1 = CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="first")
        result2 = CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="second")
        self.context.update_code_execution_result("inv-1", code_blocks1, result1)
        self.context.update_code_execution_result("inv-1", code_blocks2, result2)

        results = self.session_state["code_execution"]["code_execution_results"]
        assert len(results["inv-1"]) == 2

    def test_update_code_execution_result_with_stderr(self):
        """Test updating code execution result with stderr."""
        code_blocks = [CodeBlock(language="python", code="invalid code")]
        result = CodeExecutionResult(outcome=Outcome.OUTCOME_FAILED, output="Error occurred")
        self.context.update_code_execution_result("inv-1", code_blocks, result)

        results = self.session_state["code_execution"]["code_execution_results"]
        assert results["inv-1"][0]["result"]["outcome"] == Outcome.OUTCOME_FAILED
        assert results["inv-1"][0]["result"]["output"] == "Error occurred"

    def test_get_state_delta(self):
        """Test getting state delta."""
        self.context.set_execution_id("exec-123")
        self.context.add_input_files([CodeFile(name="test.txt", content="test", mime_type="text/plain")])
        self.context.increment_error_count("inv-1")

        delta = self.context.get_state_delta()

        assert "code_execution" in delta
        assert delta["code_execution"]["execution_id"] == "exec-123"
        assert len(delta["code_execution"]["input_files"]) == 1
        assert delta["code_execution"]["error_counts"]["inv-1"] == 1
