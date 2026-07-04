import warnings

import pytest
import yaml

from trpc_agent_sdk.tools.safety import ToolSafetyPolicy


def write_policy(tmp_path, data):
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_strict_policy_rejects_unknown_key(tmp_path):
    path = write_policy(tmp_path, {"allowed_domans": ["api.example.com"]})
    with pytest.raises(ValueError, match="unknown policy key"):
        ToolSafetyPolicy.from_file(path, strict=True)


def test_default_policy_warns_for_unknown_key(tmp_path):
    path = write_policy(tmp_path, {"allowed_domans": ["api.example.com"]})
    with pytest.warns(UserWarning, match="unknown policy key"):
        policy = ToolSafetyPolicy.from_file(path)
    assert "api.example.com" in policy.allowed_domains


def test_negative_timeout_rejected_in_strict_policy(tmp_path):
    path = write_policy(tmp_path, {"max_timeout_seconds": -1})
    with pytest.raises(ValueError, match="max_timeout_seconds"):
        ToolSafetyPolicy.from_file(path, strict=True)


def test_allowed_domains_must_be_list(tmp_path):
    path = write_policy(tmp_path, {"allowed_domains": "api.example.com"})
    with pytest.raises(ValueError, match="allowed_domains"):
        ToolSafetyPolicy.from_file(path, strict=True)


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
