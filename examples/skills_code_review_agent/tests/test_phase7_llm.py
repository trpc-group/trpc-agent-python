#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Phase 7 — LLM integration tests.

Guarantees:
  * No API key / LLM_ENABLED=false -> FakeLlm (no model dependency).
  * FakeLlm path leaves the dedupe result untouched (dry-run-safe).
  * RealLlm parses verdicts, promotes/drops findings, and degrades on failure.
The whole suite stays runnable with NO real API key.
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

_HERE = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = _HERE / "skills" / "code-review" / "scripts"
for _p in (str(_HERE), str(_SCRIPTS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dedupe import DedupeResult, Finding  # noqa: E402
from agent.llm import FakeLlm, LlmTriage, RealLlm, get_llm_client, load_llm_config  # noqa: E402
from agent.llm.client import _parse_verdicts  # noqa: E402


def _mk_finding(idx=0, conf=0.4, source="rule"):
    return Finding(
        severity="medium", category="security", file=f"a{idx}.py", line=idx,
        title=f"t{idx}", evidence="e", recommendation="r",
        confidence=conf, source=source)


def _mk_dr(n=3):
    dr = DedupeResult()
    dr.needs_human_review = [_mk_finding(i) for i in range(n)]
    return dr


class TestConfigDisable(unittest.TestCase):
    """No-key / disabled paths must yield FakeLlm and stay model-free.

    These tests assert the *resolved* config contains no API key. Because the
    project ships a real ``.env`` (with a key for live verification), we must
    isolate config loading from it: point ``env_path`` at an empty temp file
    and clear the key from ``os.environ`` so ``load_dotenv(override=False)``
    cannot re-inject it.
    """

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ("LLM_API_KEY", "LLM_ENABLED")}
        # Empty .env so load_dotenv re-injects nothing from the project file.
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False, encoding="utf-8")
        self._tmp.write("# isolated\n")
        self._tmp.close()
        os.environ.pop("LLM_API_KEY", None)

    def tearDown(self):
        os.unlink(self._tmp.name)
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_no_key_returns_fake(self):
        os.environ["LLM_ENABLED"] = "false"
        c = get_llm_client(env_path=self._tmp.name)
        self.assertIsInstance(c, FakeLlm)
        self.assertFalse(c.is_enabled)

    def test_enabled_but_no_key_returns_fake(self):
        os.environ["LLM_ENABLED"] = "true"
        c = get_llm_client(env_path=self._tmp.name)
        self.assertIsInstance(c, FakeLlm)

    def test_explicit_enable_flag_no_key_returns_fake(self):
        os.environ["LLM_ENABLED"] = "false"
        c = get_llm_client(enable=True, env_path=self._tmp.name)
        self.assertIsInstance(c, FakeLlm)


class TestFakeTriage(unittest.TestCase):
    def test_fake_noop_passthrough(self):
        dr = _mk_dr(3)
        out = asyncio.run(LlmTriage(FakeLlm()).run(dr, "some diff"))
        self.assertEqual(len(out.needs_human_review), 3)
        self.assertEqual(len(out.findings), 0)

    def test_empty_findings(self):
        dr = DedupeResult()
        out = asyncio.run(LlmTriage(FakeLlm()).run(dr, "x"))
        self.assertEqual(out.total, 0)

    def test_disabled_client_short_circuits(self):
        dr = _mk_dr(2)
        out = asyncio.run(LlmTriage(FakeLlm()).run(dr, "x"))
        self.assertIs(out, dr)  # returns the same object, no copy


class TestParse(unittest.TestCase):
    def test_parse_clean(self):
        content = json.dumps(
            {"verdicts": [{"index": 0, "verdict": "real",
                           "confidence": 0.95, "explanation": "x"}]})
        vs = _parse_verdicts(content)
        self.assertEqual(len(vs), 1)
        self.assertEqual(vs[0]["verdict"], "real")
        self.assertAlmostEqual(vs[0]["confidence"], 0.95)

    def test_parse_fenced(self):
        content = "```json\n" + json.dumps(
            {"verdicts": [{"index": 1, "verdict": "false_positive",
                           "confidence": 0.2}]}) + "\n```"
        vs = _parse_verdicts(content)
        self.assertEqual(len(vs), 1)
        self.assertEqual(vs[0]["index"], 1)
        self.assertEqual(vs[0]["verdict"], "false_positive")

    def test_parse_garbage(self):
        self.assertEqual(_parse_verdicts("not json at all"), [])

    def test_parse_clamps_confidence(self):
        content = json.dumps(
            {"verdicts": [{"index": 0, "verdict": "real", "confidence": 5.0}]})
        vs = _parse_verdicts(content)
        self.assertAlmostEqual(vs[0]["confidence"], 1.0)


# --- helpers for RealLlm with an injected fake OpenAI client -------------- #

class _FakeResp:
    def __init__(self, content):
        self.choices = [
            type("C", (), {"message": type("M", (), {"content": content})()})()
        ]


class _FakeAsyncClient:
    def __init__(self, content):
        self._content = content
        self.chat = type("Chat", (), {"completions": self})()

    async def create(self, **kw):
        return _FakeResp(self._content)


class TestRealTriage(unittest.TestCase):
    def _client(self, content):
        cfg = load_llm_config()
        cfg.api_key = "sk-test"
        cfg.enabled = True
        return RealLlm(cfg, client=_FakeAsyncClient(content))

    def test_real_promotes_and_drops(self):
        content = json.dumps(
            {"verdicts": [
                {"index": 0, "verdict": "real", "confidence": 0.92,
                 "explanation": "confirmed SQLi"},
                {"index": 1, "verdict": "false_positive", "confidence": 0.1},
                {"index": 2, "verdict": "real", "confidence": 0.7,
                 "explanation": "likely ok"},
            ]})
        dr = _mk_dr(3)
        out = asyncio.run(
            LlmTriage(self._client(content)).run(
                dr, "diff with secret sk-abcdefghijklmnopqrstuvwxyz"))
        # index 1 dropped -> 2 remain for review... but index 2 is promoted to
        # warnings (0.7), so only index 0 (promoted to findings) leaves review.
        self.assertEqual(len(out.needs_human_review), 0)
        self.assertEqual(len(out.findings), 1)
        self.assertEqual(len(out.warnings), 1)
        f0 = out.findings[0]
        self.assertIn("llm", f0.source)
        self.assertIn("[LLM]", f0.recommendation)

    def test_real_failure_degrades(self):
        class _BadClient:
            def __init__(self):
                self.chat = type("C", (), {"completions": self})()

            async def create(self, **kw):
                raise RuntimeError("boom")

        cfg = load_llm_config()
        cfg.api_key = "sk-test"
        cfg.enabled = True
        c = RealLlm(cfg, client=_BadClient())
        dr = _mk_dr(2)
        out = asyncio.run(LlmTriage(c).run(dr, "x"))
        # call failed -> degrade to no-op, original result untouched
        self.assertEqual(len(out.needs_human_review), 2)

    def test_real_masks_diff_before_send(self):
        sent = {}

        class _CapturingClient:
            def __init__(self):
                self.chat = type("C", (), {"completions": self})()

            async def create(self, **kw):
                sent["messages"] = kw["messages"]
                return _FakeResp(json.dumps({"verdicts": []}))

        cfg = load_llm_config()
        cfg.api_key = "sk-test"
        cfg.enabled = True
        c = RealLlm(cfg, client=_CapturingClient())
        asyncio.run(
            LlmTriage(c).run(_mk_dr(1),
                             "leak sk-abcdefghijklmnopqrstuvwxyz here"))
        user_msg = sent["messages"][1]["content"]
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", user_msg)
        self.assertIn("REDACTED", user_msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
