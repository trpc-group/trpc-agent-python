#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

import json
from pathlib import Path

import pytest

from examples.skills_code_review_agent.code_review.config import ReviewConfig


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_review_config_defaults_match_locked_spec() -> None:
    config = ReviewConfig()

    assert config.max_input_file_bytes == 1024 * 1024
    assert config.max_input_files == 500
    assert config.max_input_bytes == 10 * 1024 * 1024
    assert config.max_diff_lines == 50_000
    assert config.max_sandbox_runs == 10
    assert config.per_run_timeout_seconds == 30
    assert config.sandbox_time_budget_seconds == 90
    assert config.review_deadline_seconds == 110
    assert config.max_output_bytes_per_run == 1024 * 1024
    assert config.max_output_bytes_per_review == 2 * 1024 * 1024
    assert config.network_policy == "deny"
    assert config.schema_version == "1.0.0"
    assert config.rule_pack_version == "1.0.0"


def test_review_config_supports_typed_environment_overrides() -> None:
    config = ReviewConfig.from_env(
        {
            "CODE_REVIEW_MAX_INPUT_FILES": "24",
            "CODE_REVIEW_PER_RUN_TIMEOUT_SECONDS": "12",
            "CODE_REVIEW_SCHEMA_VERSION": "1.1.0",
        }
    )

    assert config.max_input_files == 24
    assert config.per_run_timeout_seconds == 12
    assert config.schema_version == "1.1.0"


def test_review_config_rejects_invalid_environment_values() -> None:
    with pytest.raises(ValueError, match="CODE_REVIEW_MAX_INPUT_FILES"):
        ReviewConfig.from_env({"CODE_REVIEW_MAX_INPUT_FILES": "many"})

    with pytest.raises(ValueError, match="max_input_files"):
        ReviewConfig(max_input_files=0)


def test_config_digest_is_stable_and_sensitive_to_values() -> None:
    first = ReviewConfig.from_env(
        {
            "CODE_REVIEW_MAX_INPUT_FILES": "24",
            "CODE_REVIEW_PER_RUN_TIMEOUT_SECONDS": "12",
        }
    )
    reordered = ReviewConfig.from_env(
        {
            "CODE_REVIEW_PER_RUN_TIMEOUT_SECONDS": "12",
            "CODE_REVIEW_MAX_INPUT_FILES": "24",
        }
    )
    changed = ReviewConfig(max_input_files=25, per_run_timeout_seconds=12)

    assert first.config_digest == reordered.config_digest
    assert len(first.config_digest) == 64
    assert first.config_digest != changed.config_digest
    assert first.to_dict()["config_digest"] == first.config_digest


def test_report_schema_is_valid_json_schema_document() -> None:
    schema_path = PROJECT_ROOT / "schemas" / "review_report.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert {
        "schema_version",
        "rule_pack_version",
        "config_digest",
        "input_sha256",
        "task_id",
        "input_summary",
        "findings",
        "needs_human_review",
        "warnings",
        "suppressed",
        "filter_summary",
        "sandbox_summary",
        "metrics",
        "final_conclusion",
    } <= set(schema["required"])


def test_architecture_skeleton_is_present() -> None:
    expected_modules = {
        "run_agent.py",
        "evaluate.py",
        "agent/__init__.py",
        "agent/agent.py",
        "agent/prompts.py",
        "code_review/__init__.py",
        "code_review/config.py",
        "code_review/pipeline.py",
        "code_review/inputs.py",
        "code_review/governance.py",
        "code_review/sandbox.py",
        "code_review/redaction.py",
        "code_review/dedup.py",
        "code_review/llm_enhancer.py",
        "code_review/report.py",
        "code_review/metrics.py",
        "code_review/store/__init__.py",
        "code_review/store/models.py",
        "code_review/store/review_store.py",
        "code_review/store/init_db.py",
        "skills/code-review/scripts/parse_diff.py",
        "skills/code-review/scripts/run_checks.py",
        "skills/code-review/scripts/lib/__init__.py",
        "skills/code-review/scripts/lib/diff_parser.py",
        "skills/code-review/scripts/lib/rule_engine.py",
        "skills/code-review/scripts/lib/rules_security.py",
        "skills/code-review/scripts/lib/rules_async.py",
        "skills/code-review/scripts/lib/rules_resource.py",
        "skills/code-review/scripts/lib/rules_db.py",
        "skills/code-review/scripts/lib/rules_tests.py",
        "skills/code-review/scripts/lib/secret_rules.py",
    }
    expected_directories = {
        "sample_output",
        "skills/code-review/references",
        "skills/code-review/rules",
        "tests/e2e",
        "tests/fixtures/corpus",
        "tests/fixtures/diffs",
        "tests/integration",
        "tests/support",
        "tests/unit",
    }

    assert Path(__file__).parent.name == "unit"
    assert not (PROJECT_ROOT / "fixtures").exists()
    assert not [path for path in expected_modules if not (PROJECT_ROOT / path).is_file()]
    assert not [path for path in expected_directories if not (PROJECT_ROOT / path).is_dir()]
