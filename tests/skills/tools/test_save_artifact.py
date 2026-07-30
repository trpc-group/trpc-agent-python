from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.skills._constants import SKILL_ARTIFACTS_STATE_KEY
from trpc_agent_sdk.skills.tools._save_artifact import SaveArtifactTool
from trpc_agent_sdk.skills.tools._save_artifact import _apply_artifact_state_delta
from trpc_agent_sdk.skills.tools._save_artifact import _artifact_save_skip_reason
from trpc_agent_sdk.skills.tools._save_artifact import _normalize_artifact_path
from trpc_agent_sdk.skills.tools._save_artifact import _normalize_workspace_prefix


def test_normalize_workspace_prefix():
    assert _normalize_workspace_prefix("workspace://work/a.txt") == "work/a.txt"
    assert _normalize_workspace_prefix("$WORK_DIR/a.txt") == "work/a.txt"


def test_normalize_artifact_path_valid_and_invalid(tmp_path: Path):
    workspace_root = str(tmp_path)
    rel, abs_path = _normalize_artifact_path("work/a.txt", workspace_root)
    assert rel == "work/a.txt"
    assert abs_path.endswith("work/a.txt")

    with pytest.raises(ValueError, match="stay within the workspace"):
        _normalize_artifact_path("../a.txt", workspace_root)


def test_artifact_save_skip_reason_and_state_delta():
    ctx = MagicMock()
    ctx.artifact_service = object()
    ctx.session = object()
    ctx.app_name = "app"
    ctx.user_id = "u"
    ctx.session_id = "s"
    ctx.function_call_id = "fc-1"
    ctx.actions.state_delta = {}
    assert _artifact_save_skip_reason(ctx) == ""

    _apply_artifact_state_delta(ctx, "work/a.txt", 2, "artifact://work/a.txt@2")
    value = ctx.actions.state_delta[SKILL_ARTIFACTS_STATE_KEY]
    assert value["tool_call_id"] == "fc-1"
    assert value["artifacts"][0]["version"] == 2


def test_save_artifact_declaration_name():
    declaration = SaveArtifactTool()._get_declaration()
    assert declaration is not None
    assert declaration.name == "workspace_save_artifact"
