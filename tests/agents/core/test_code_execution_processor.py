# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for code execution processor utilities."""

from __future__ import annotations

import pytest

from trpc_agent_sdk.agents.core._code_execution_processor import (
    DataFileUtil,
    _DATA_FILE_UTIL_MAP,
    _get_data_file_preprocessing_code,
)
from trpc_agent_sdk.code_executors import CodeFile


# ---------------------------------------------------------------------------
# DataFileUtil
# ---------------------------------------------------------------------------


class TestDataFileUtil:
    def test_csv_extension(self):
        util = _DATA_FILE_UTIL_MAP["text/csv"]
        assert util.extension == ".csv"

    def test_csv_loader_template(self):
        util = _DATA_FILE_UTIL_MAP["text/csv"]
        assert "pd.read_csv" in util.loader_code_template

    def test_data_file_util_fields(self):
        util = DataFileUtil(extension=".json", loader_code_template="json.load('{filename}')")
        assert util.extension == ".json"
        assert "json.load" in util.loader_code_template


# ---------------------------------------------------------------------------
# _get_data_file_preprocessing_code
# ---------------------------------------------------------------------------


class TestGetDataFilePreprocessingCode:
    def test_csv_file_generates_code(self):
        file = CodeFile(name="data_1_1.csv", content="a,b\n1,2", mime_type="text/csv")
        code = _get_data_file_preprocessing_code(file)
        assert code is not None
        assert "pd.read_csv" in code
        assert "explore_df" in code
        assert "data_1_1" in code

    def test_unsupported_mime_returns_none(self):
        file = CodeFile(name="test.json", content="{}", mime_type="application/json")
        code = _get_data_file_preprocessing_code(file)
        assert code is None

    def test_filename_starting_with_digit(self):
        file = CodeFile(name="1data.csv", content="a,b\n1,2", mime_type="text/csv")
        code = _get_data_file_preprocessing_code(file)
        assert code is not None
        assert "_1data" in code

    def test_filename_with_special_chars(self):
        file = CodeFile(name="my-data.file.csv", content="a,b\n1,2", mime_type="text/csv")
        code = _get_data_file_preprocessing_code(file)
        assert code is not None
        assert "my_data_file" in code


# ---------------------------------------------------------------------------
# _DATA_FILE_UTIL_MAP
# ---------------------------------------------------------------------------


class TestDataFileUtilMap:
    def test_text_csv_present(self):
        assert "text/csv" in _DATA_FILE_UTIL_MAP

    def test_csv_has_correct_extension(self):
        assert _DATA_FILE_UTIL_MAP["text/csv"].extension == ".csv"
