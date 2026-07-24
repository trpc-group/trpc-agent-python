import warnings
from pathlib import Path

import pytest
import yaml

from trpc_agent_sdk.tools.safety import ToolSafetyPolicy

CANONICAL_POLICY = Path("examples/tool_safety/tool_safety_policy.yaml")
ALIAS_POLICY = Path("examples/tool_safety/policy.yaml")


def write_policy(tmp_path, data):
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_strict_policy_rejects_unknown_key(tmp_path):
    path = write_policy(tmp_path, {"allowed_domans": ["api.example.com"]})
    with pytest.raises(ValueError, match="unknown policy key"):
        ToolSafetyPolicy.from_file(path, strict=True)


def test_default_policy_warns_for_unknown_key(tmp_path):
    path = write_policy(tmp_path, {"allowed_domans": ["typo-only.example"]})
    with pytest.warns(UserWarning, match="unknown policy key"):
        policy = ToolSafetyPolicy.from_file(path)
    assert "typo-only.example" not in policy.allowed_domains
    with pytest.raises(ValueError, match="unknown policy key"):
        ToolSafetyPolicy.from_file(path, strict=True)


def test_negative_timeout_rejected_in_strict_policy(tmp_path):
    path = write_policy(tmp_path, {"max_timeout_seconds": -1})
    with pytest.raises(ValueError, match="max_timeout_seconds"):
        ToolSafetyPolicy.from_file(path, strict=True)


def test_allowed_domains_must_be_list(tmp_path):
    path = write_policy(tmp_path, {"allowed_domains": "api.example.com"})
    with pytest.raises(ValueError, match="allowed_domains"):
        ToolSafetyPolicy.from_file(path, strict=True)


def test_policy_yaml_must_be_mapping(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        ToolSafetyPolicy.from_file(path, strict=True)


def test_empty_policy_yaml_must_be_mapping(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="YAML mapping"):
        ToolSafetyPolicy.from_file(path)


def test_string_list_fields_accept_strings_without_extra_shape_checks(tmp_path):
    path = write_policy(tmp_path, {"allowed_commands": ["python", ""]})
    policy = ToolSafetyPolicy.from_file(path, strict=True)
    assert policy.allowed_commands == ["python", ""]


def test_bool_policy_field_type_rejected_in_strict_policy(tmp_path):
    path = write_policy(tmp_path, {"review_dynamic_code": "yes"})
    with pytest.raises(ValueError, match="review_dynamic_code"):
        ToolSafetyPolicy.from_file(path, strict=True)


def test_default_policy_warns_and_ignores_invalid_field_values(tmp_path):
    path = write_policy(
        tmp_path,
        {
            "allowed_domains": "api.example.com",
            "max_timeout_seconds": -1,
            "review_dynamic_code": "yes",
        },
    )

    with pytest.warns(UserWarning) as caught:
        policy = ToolSafetyPolicy.from_file(path)

    messages = [str(warning.message) for warning in caught]
    assert any("allowed_domains" in message for message in messages)
    assert any("max_timeout_seconds" in message for message in messages)
    assert any("review_dynamic_code" in message for message in messages)
    assert policy.allowed_domains == ToolSafetyPolicy.default().allowed_domains
    assert policy.max_timeout_seconds == ToolSafetyPolicy.default().max_timeout_seconds
    assert policy.review_dynamic_code == ToolSafetyPolicy.default().review_dynamic_code


def test_normal_policy_loads_without_warnings(tmp_path):
    path = write_policy(
        tmp_path,
        {
            "allowed_domains": ["api.example.com"],
            "allowed_commands": ["python", "bash"],
            "max_timeout_seconds": 120,
        },
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        policy = ToolSafetyPolicy.from_file(path, strict=True)
    assert not caught
    assert policy.max_timeout_seconds == 120
    assert policy.allowed_commands == ["python", "bash"]


def test_example_policy_alias_matches_canonical_policy():
    assert ALIAS_POLICY.read_text(encoding="utf-8") == CANONICAL_POLICY.read_text(encoding="utf-8")
