"""LLM second-opinion triage over low-confidence findings (Phase 7).

Runs *after* dedupe and *before* persistence. The model only sees the
``needs_human_review`` bucket (low confidence). Verdicts are applied to:

* **false_positive** -> dropped entirely
* **real** -> confidence updated, ``source`` tagged ``+llm``, explanation
  appended to ``recommendation``, and re-bucketed by the (model-given)
  confidence (>=0.8 -> findings, >=0.6 -> warnings, else stays in review).

When the client is disabled or the call fails, the original
:class:`DedupeResult` is returned unchanged (no-op degradation).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cr_models import DedupeResult
    from .client import LlmClient


def _apply_verdicts(dr: "DedupeResult", verdicts: list[dict]) -> "DedupeResult":
    by_idx = {v["index"]: v for v in verdicts}
    kept: list = []
    promoted_findings: list = []
    promoted_warnings: list = []

    for i, f in enumerate(dr.needs_human_review):
        v = by_idx.get(i)
        if v is None:
            kept.append(f)
            continue
        if v.get("verdict") == "false_positive":
            continue  # drop
        conf = v.get("confidence", f.confidence)
        f.confidence = max(0.0, min(1.0, conf))
        if "llm" not in f.source:
            f.source = (f.source + "+llm") if f.source else "llm"
        expl = (v.get("explanation") or "").strip()
        if expl:
            f.recommendation = f.recommendation + f"\n[LLM] {expl}"
        if f.confidence >= 0.8:
            promoted_findings.append(f)
        elif f.confidence >= 0.6:
            promoted_warnings.append(f)
        else:
            kept.append(f)

    dr.findings = list(dr.findings) + promoted_findings
    dr.warnings = list(dr.warnings) + promoted_warnings
    dr.needs_human_review = kept
    return dr


class LlmTriage:
    """Drives the optional LLM second-opinion pass."""

    def __init__(self, client: "LlmClient"):
        self.client = client

    async def run(self, dedupe_result: "DedupeResult", diff_text: str) -> "DedupeResult":
        nh = dedupe_result.needs_human_review
        if not nh or not self.client.is_enabled:
            return dedupe_result
        # Mask secrets before sending the diff to the model.
        from mask_secrets import mask_secrets

        masked, _ = mask_secrets(diff_text or "")
        verdicts = await self.client.triage(nh, masked)
        if not verdicts:
            return dedupe_result
        return _apply_verdicts(dedupe_result, verdicts)
