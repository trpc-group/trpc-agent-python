#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Phase 7 — LIVE real-LLM integration test (gated, network + key required).

This is the acceptance test that proves the real OpenAI-compatible endpoint
actually judges the low-confidence ``needs_human_review`` bucket. It is
deliberately DISABLED by default so the suite stays key-free and offline:

    # run it (needs a configured LLM_API_KEY in .env):
    CR_LLM_LIVE=1 python tests/test_phase7_llm_live.py

Verified against DeepSeek (https://api.deepseek.com, model deepseek-chat):
the model returns a verdict for the SEC005 (verify=False) candidate, and the
triage promotes/drops it and tags ``source`` with ``llm``.
"""
import asyncio
import os
import sys
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
_SCRIPTS = _HERE / "skills" / "code-review" / "scripts"
for _p in (str(_HERE), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dedupe import DedupeResult, Finding  # noqa: E402
from agent.llm import LlmTriage, RealLlm, load_llm_config  # noqa: E402

_LLM_LIVE = os.getenv("CR_LLM_LIVE") == "1"
_cfg = load_llm_config()


@unittest.skipUnless(
    _LLM_LIVE and _cfg.has_key,
    "set CR_LLM_LIVE=1 and configure LLM_API_KEY in .env to run the live LLM test",
)
class TestLiveLlmTriage(unittest.TestCase):
    def _make_dr(self) -> DedupeResult:
        dr = DedupeResult()
        dr.needs_human_review.append(
            Finding(
                severity="medium",
                category="security",
                file="svc/client.py",
                line=7,
                title="SEC005: TLS 证书校验被关闭 (verify=False)",
                evidence="requests.post(endpoint, json=payload, verify=False)",
                recommendation="检查 TLS 校验配置",
                confidence=0.5,  # < 0.6 -> lands in needs_human_review
                source="rule",
            )
        )
        return dr

    def test_live_client_returns_verdict(self):
        client = RealLlm(_cfg)
        dr = self._make_dr()
        diff = (
            "diff --git a/svc/client.py b/svc/client.py\n"
            "--- /dev/null\n"
            "+++ b/svc/client.py\n"
            "@@ -0,0 +1,7 @@\n"
            "+    return requests.post(endpoint, json=payload, verify=False)\n"
        )
        verdicts = asyncio.run(client.triage(dr.needs_human_review, diff))
        self.assertTrue(verdicts, "live model should return at least one verdict")
        for v in verdicts:
            self.assertIn(v["verdict"], ("real", "false_positive"))
            self.assertGreaterEqual(v["confidence"], 0.0)
            self.assertLessEqual(v["confidence"], 1.0)

    def test_live_triage_resolves_low_confidence(self):
        client = RealLlm(_cfg)
        dr = self._make_dr()
        diff = (
            "diff --git a/svc/client.py b/svc/client.py\n"
            "+    return requests.post(endpoint, json=payload, verify=False)\n"
        )
        out = asyncio.run(LlmTriage(client).run(dr, diff))
        # The low-confidence item must be resolved (not left in review).
        self.assertEqual(
            len(out.needs_human_review), 0,
            "low-confidence item must be resolved by the LLM",
        )
        total = out.total
        self.assertIn(total, (0, 1))  # dropped (0) or kept/promoted (1)
        if total == 1:
            kept = out.findings + out.warnings
            self.assertTrue(kept)
            self.assertIn("llm", kept[0].source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
