# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.utils._json_repair.

Covers:
- json_loads_repair: parses valid JSON, repairs malformed JSON, decodes bytes,
  forwards kwargs, raises JSONDecodeError when unrecoverable.
- json_repair_string: returns a valid JSON string for valid/malformed inputs,
  decodes bytes, forwards kwargs, raises JSONDecodeError when unrecoverable.
"""

import json

import pytest

from trpc_agent_sdk.utils import json_loads_repair
from trpc_agent_sdk.utils import json_repair_string


class TestJsonLoadsRepair:
    """Test suite for json_loads_repair."""

    def test_valid_json_object(self):
        assert json_loads_repair('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}

    def test_valid_json_array(self):
        assert json_loads_repair("[1, 2, 3]") == [1, 2, 3]

    def test_valid_json_scalar(self):
        assert json_loads_repair("true") is True
        assert json_loads_repair("null") is None
        assert json_loads_repair("123") == 123

    def test_missing_comma_repaired(self):
        result = json_loads_repair('{"city": "Beijing" "unit": "celsius"}')
        assert result == {"city": "Beijing", "unit": "celsius"}

    def test_trailing_comma_repaired(self):
        result = json_loads_repair('{"a": 1, "b": 2,}')
        assert result == {"a": 1, "b": 2}

    def test_single_quoted_keys_repaired(self):
        result = json_loads_repair("{'a': 'b'}")
        assert result == {"a": "b"}

    def test_unclosed_object_repaired(self):
        result = json_loads_repair('{"a": 1, "b": 2')
        assert result == {"a": 1, "b": 2}

    def test_code_fence_wrapped_json_repaired(self):
        payload = '```json\n{"a": 1}\n```'
        result = json_loads_repair(payload)
        assert result == {"a": 1}

    def test_bytes_input_decoded_and_parsed(self):
        result = json_loads_repair(b'{"a": 1}')
        assert result == {"a": 1}

    def test_bytearray_input_decoded_and_repaired(self):
        result = json_loads_repair(bytearray(b'{"a": 1 "b": 2}'))
        assert result == {"a": 1, "b": 2}

    def test_non_utf8_bytes_replaced_not_raised(self):
        # decode(errors="replace") should keep us off the exception path for
        # invalid utf-8 bytes; the repaired result is still a usable JSON value.
        result = json_loads_repair(b'\xff{"a": 1}')
        assert isinstance(result, (dict, list, str, int, float, bool)) or result is None

    def test_unicode_preserved(self):
        assert json_loads_repair('{"name": "北京"}') == {"name": "北京"}

    def test_unrecoverable_input_raises_json_decode_error(self, monkeypatch):
        import trpc_agent_sdk.utils._json_repair as module

        def _boom(*_args, **_kwargs):
            raise RuntimeError("unrecoverable")

        monkeypatch.setattr(module.json_repair, "loads", _boom)

        with pytest.raises(json.JSONDecodeError):
            json_loads_repair('{"oops"')

    def test_kwargs_forwarded_to_json_repair(self, monkeypatch):
        import trpc_agent_sdk.utils._json_repair as module

        captured = {}

        def _fake_loads(value, **kwargs):
            captured["value"] = value
            captured["kwargs"] = kwargs
            return {"ok": True}

        monkeypatch.setattr(module.json_repair, "loads", _fake_loads)

        result = json_loads_repair('{"x": 1}', skip_json_loads=True, logging=False)

        assert result == {"ok": True}
        assert captured["value"] == '{"x": 1}'
        assert captured["kwargs"] == {"skip_json_loads": True, "logging": False}


class TestJsonRepairString:
    """Test suite for json_repair_string."""

    def test_valid_json_returns_canonical_string(self):
        repaired = json_repair_string('{"a": 1, "b": "x"}')
        assert json.loads(repaired) == {"a": 1, "b": "x"}

    def test_missing_comma_repaired(self):
        repaired = json_repair_string('{"city": "Beijing" "unit": "celsius"}')
        assert json.loads(repaired) == {"city": "Beijing", "unit": "celsius"}

    def test_single_quoted_keys_repaired(self):
        repaired = json_repair_string("{'a': 'b'}")
        assert json.loads(repaired) == {"a": "b"}

    def test_unclosed_object_repaired(self):
        repaired = json_repair_string('{"a": 1, "b": 2')
        assert json.loads(repaired) == {"a": 1, "b": 2}

    def test_array_repaired(self):
        repaired = json_repair_string("[1, 2 3,]")
        assert json.loads(repaired) == [1, 2, 3]

    def test_bytes_input_decoded(self):
        repaired = json_repair_string(b'{"a": 1}')
        assert json.loads(repaired) == {"a": 1}

    def test_bytearray_input_decoded_and_repaired(self):
        repaired = json_repair_string(bytearray(b'{"a": 1 "b": 2}'))
        assert json.loads(repaired) == {"a": 1, "b": 2}

    def test_ensure_ascii_passthrough_default_preserves_unicode(self):
        repaired = json_repair_string('{"name": "北京"}', ensure_ascii=False)
        assert "北京" in repaired
        assert json.loads(repaired) == {"name": "北京"}

    def test_ensure_ascii_true_escapes_unicode(self):
        repaired = json_repair_string('{"name": "北京"}', ensure_ascii=True)
        assert "北京" not in repaired
        assert json.loads(repaired) == {"name": "北京"}

    def test_return_type_is_string(self):
        assert isinstance(json_repair_string('{"a": 1}'), str)

    def test_unrecoverable_input_raises_json_decode_error(self, monkeypatch):
        import trpc_agent_sdk.utils._json_repair as module

        def _boom(*_args, **_kwargs):
            raise RuntimeError("unrecoverable")

        monkeypatch.setattr(module.json_repair, "repair_json", _boom)

        with pytest.raises(json.JSONDecodeError):
            json_repair_string('{"oops"')

    def test_kwargs_forwarded_to_json_repair(self, monkeypatch):
        import trpc_agent_sdk.utils._json_repair as module

        captured = {}

        def _fake_repair_json(value, **kwargs):
            captured["value"] = value
            captured["kwargs"] = kwargs
            return '{"ok": true}'

        monkeypatch.setattr(module.json_repair, "repair_json", _fake_repair_json)

        repaired = json_repair_string('{"x": 1}', ensure_ascii=False, logging=False)

        assert repaired == '{"ok": true}'
        assert captured["value"] == '{"x": 1}'
        assert captured["kwargs"] == {"ensure_ascii": False, "logging": False}
