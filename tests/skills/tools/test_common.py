from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.skills.tools._common import get_staged_workspace_dir
from trpc_agent_sdk.skills.tools._common import inline_json_schema_refs
from trpc_agent_sdk.skills.tools._common import require_non_empty
from trpc_agent_sdk.skills.tools._common import set_staged_workspace_dir


def test_require_non_empty():
    assert require_non_empty("  ok  ", field_name="x") == "ok"
    with pytest.raises(ValueError, match="x is required"):
        require_non_empty(" ", field_name="x")


def test_inline_json_schema_refs():
    schema = {"$defs": {"S": {"type": "string"}}, "properties": {"name": {"$ref": "#/$defs/S"}}}
    out = inline_json_schema_refs(schema)
    assert "$defs" not in out
    assert out["properties"]["name"]["type"] == "string"


def test_staged_workspace_dir_round_trip():
    metadata = {}
    ctx = MagicMock()
    ctx.agent_context.get_metadata = MagicMock(side_effect=lambda key, default=None: metadata.get(key, default))
    ctx.agent_context.with_metadata = MagicMock(side_effect=lambda key, value: metadata.__setitem__(key, value))

    set_staged_workspace_dir(ctx, "skill-a", "skills/skill-a")
    assert get_staged_workspace_dir(ctx, "skill-a") == "skills/skill-a"
