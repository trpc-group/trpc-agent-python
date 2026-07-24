# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency test framework for session and memory backends."""

from .constants import ALLOWED_DIFFS
from .constants import EXPECTED_REPLAY_CASE_FILES
from .constants import EXPECTED_REPLAY_CASE_NAMES
from .constants import REPLAY_CASES_DIR
from .constants import SUMMARY_TRUNCATION_ALLOWED_DIFF_PATHS
from .constants import SUMMARY_TRUNCATION_ALLOWED_DIFF_REASON
from .models import ReplayCase
from .loaders import MEMORY_REPLAY_CASES
from .loaders import REPLAY_CASES
from .loaders import load_jsonl_records
from .loaders import load_replay_cases
from .backends import BASELINE_BACKEND_NAME
from .backends import DEFAULT_BACKEND_CONFIG
from .backends import ENV_REDIS_BACKEND_NAME
from .backends import ENV_SQL_BACKEND_NAME
from .backends import MOCK_REDIS_BACKEND_NAME
from .backends import REDIS_URL_ENV
from .backends import SQLITE_BACKEND_NAME
from .backends import SQL_URL_ENV
from .backends import ReplayBackendConfig
from .backends import ReplayBackendUnavailable
from .backends import comparison_backend_names
from .backends import configured_memory_backend_names
from .backends import configured_session_backend_names
from .backends import create_sql_memory_service
from .backends import create_sql_session_service
from .backends import default_backend_matrix_enabled
from .backends import get_required_session
from .backends import make_memory_session
from .backends import resolve_backend_config
from .backends import run_memory_replay_case
from .backends import run_session_replay_case
from .fixtures import make_memory_config
from .fixtures import make_session_config
from .normalizers import all_normalized_events
from .normalizers import canonicalize
from .normalizers import compact_text
from .normalizers import event_texts
from .normalizers import make_event
from .normalizers import make_part
from .normalizers import normalize_content_parts
from .normalizers import normalize_event
from .normalizers import normalize_memory_entry
from .normalizers import normalize_memory_response
from .normalizers import normalize_part
from .normalizers import normalize_session
from .normalizers import normalize_summary_text
from .normalizers import summary_events
from .normalizers import summary_metadata
from .normalizers import summary_records_by_id
from .normalizers import summary_text
from .comparators import allowed_diff_reason
from .comparators import diff_dicts
from .comparators import diff_lists
from .comparators import diff_snapshots
from .comparators import event_at
from .comparators import extract_event_location
from .comparators import extract_summary_id
from .comparators import find_summary_id
from .comparators import is_event_diff
from .comparators import is_session_metadata_diff
from .comparators import is_state_diff
from .comparators import is_summary_diff
from .comparators import join_path
from .comparators import make_diff
from .reporters import build_case_diff_report
from .reporters import build_case_memory_report
from .reporters import build_case_session_report
from .reporters import build_diff_report
from .reporters import build_replay_diff_report
from .reporters import build_report_totals
from .reporters import build_summary_content_checks
from .reporters import build_summary_metadata_checks
from .reporters import combined_status
from .reporters import count_case_diffs
from .reporters import count_summary_check_mismatches
from .reporters import diff_status
from .reporters import iter_case_diffs
from .reporters import serialize_allowed_diffs
from .reporters import session_report_status
from .reporters import summary_comparison_records
from .reporters import summary_cache_records_by_id
from .reporters import summary_metadata_field_check
from .assertions import assert_all_diffs_allowed
from .assertions import assert_allowed_session_snapshot_variant
from .assertions import assert_memory_replay_case_snapshot
from .assertions import assert_replay_case_fixtures_load
from .assertions import assert_session_replay_case_snapshot
from .assertions import assert_summary_truncation_in_memory_snapshot
from .assertions import uses_allowed_snapshot_variant

__all__ = [
    # Constants
    "REPLAY_CASES_DIR",
    "EXPECTED_REPLAY_CASE_NAMES",
    "EXPECTED_REPLAY_CASE_FILES",
    "SUMMARY_TRUNCATION_ALLOWED_DIFF_REASON",
    "SUMMARY_TRUNCATION_ALLOWED_DIFF_PATHS",
    "ALLOWED_DIFFS",
    # Models
    "ReplayCase",
    # Loaders
    "load_jsonl_records",
    "load_replay_cases",
    "REPLAY_CASES",
    "MEMORY_REPLAY_CASES",
    # Fixtures
    "make_session_config",
    "make_memory_config",
    # Backends
    "BASELINE_BACKEND_NAME",
    "SQLITE_BACKEND_NAME",
    "ENV_SQL_BACKEND_NAME",
    "ENV_REDIS_BACKEND_NAME",
    "MOCK_REDIS_BACKEND_NAME",
    "DEFAULT_BACKEND_CONFIG",
    "SQL_URL_ENV",
    "REDIS_URL_ENV",
    "ReplayBackendConfig",
    "ReplayBackendUnavailable",
    "comparison_backend_names",
    "configured_session_backend_names",
    "configured_memory_backend_names",
    "default_backend_matrix_enabled",
    "resolve_backend_config",
    "create_sql_session_service",
    "create_sql_memory_service",
    "get_required_session",
    "make_memory_session",
    "run_session_replay_case",
    "run_memory_replay_case",
    # Normalizers
    "make_event",
    "make_part",
    "normalize_session",
    "normalize_event",
    "normalize_content_parts",
    "normalize_part",
    "normalize_memory_response",
    "normalize_memory_entry",
    "summary_events",
    "event_texts",
    "all_normalized_events",
    "compact_text",
    "normalize_summary_text",
    "canonicalize",
    "summary_metadata",
    "summary_records_by_id",
    "summary_text",
    # Comparators
    "diff_snapshots",
    "diff_dicts",
    "diff_lists",
    "join_path",
    "make_diff",
    "allowed_diff_reason",
    "extract_event_location",
    "extract_summary_id",
    "event_at",
    "find_summary_id",
    "is_event_diff",
    "is_state_diff",
    "is_summary_diff",
    "is_session_metadata_diff",
    # Reporters
    "build_replay_diff_report",
    "build_case_diff_report",
    "build_case_session_report",
    "build_case_memory_report",
    "build_report_totals",
    "build_diff_report",
    "build_summary_content_checks",
    "build_summary_metadata_checks",
    "summary_metadata_field_check",
    "summary_comparison_records",
    "summary_cache_records_by_id",
    "session_report_status",
    "combined_status",
    "diff_status",
    "count_summary_check_mismatches",
    "count_case_diffs",
    "iter_case_diffs",
    "serialize_allowed_diffs",
    # Assertions
    "assert_all_diffs_allowed",
    "assert_replay_case_fixtures_load",
    "assert_session_replay_case_snapshot",
    "assert_allowed_session_snapshot_variant",
    "assert_summary_truncation_in_memory_snapshot",
    "assert_memory_replay_case_snapshot",
    "uses_allowed_snapshot_variant",
]
