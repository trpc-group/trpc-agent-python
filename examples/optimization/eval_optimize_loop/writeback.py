# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""Post-Gate source prompt writeback with drift detection and verification."""

from __future__ import annotations

from hashlib import sha256

from trpc_agent_sdk.evaluation import TargetPrompt

from .candidate_provider import prompt_mapping_sha256
from .config import WritebackConfig
from .prompt_workspace import SourcePromptDriftError
from .prompt_workspace import verify_source_hashes
from .schemas import CandidateProposal
from .schemas import GateDecision
from .schemas import PromptSnapshot
from .schemas import WritebackResult


class WritebackIntegrityError(RuntimeError):
    """The pipeline cannot prove that source prompts remain in a safe state."""


def _field_hashes(prompts: dict[str, str]) -> dict[str, str]:
    return {
        name: sha256(content.encode("utf-8")).hexdigest()
        for name, content in prompts.items()
    }


async def _blocked_for_drift(
    source_target: TargetPrompt,
    message: str,
) -> WritebackResult:
    try:
        observed = await source_target.read_all()
    except Exception:
        observed = {}
    return WritebackResult(
        status="blocked",
        reason="source_drift",
        source_hashes_before=_field_hashes(observed),
        error_message=message,
    )


async def _restore_and_verify(
    source_target: TargetPrompt,
    baseline: dict[str, str],
) -> dict[str, str]:
    """Restore only when needed, then prove the exact baseline is present."""
    try:
        current = await source_target.read_all()
    except Exception:
        current = None
    if current != baseline:
        try:
            await source_target.write_all(baseline)
        except Exception as exc:
            raise WritebackIntegrityError(f"source prompt rollback failed: {exc}") from exc
    try:
        restored = await source_target.read_all()
    except Exception as exc:
        raise WritebackIntegrityError(f"failed to verify source prompt rollback: {exc}") from exc
    if restored != baseline:
        raise WritebackIntegrityError("source prompts do not match the pre-write snapshot after rollback")
    return restored


async def perform_writeback(
    *,
    decision: GateDecision,
    config: WritebackConfig,
    snapshots: list[PromptSnapshot],
    source_target: TargetPrompt,
    candidate: CandidateProposal,
) -> WritebackResult:
    """Apply a candidate only after ACCEPT and return a structured outcome."""
    if decision.decision == "reject":
        return WritebackResult(status="skipped", reason="gate_rejected")
    if not config.enabled:
        return WritebackResult(status="skipped", reason="disabled")
    if not config.require_source_hash_match:
        raise WritebackIntegrityError("enabled writeback requires source hash verification")
    if prompt_mapping_sha256(candidate.prompts) != candidate.candidate_prompt_sha256:
        raise WritebackIntegrityError("candidate prompt hash does not match its prompt payload")

    try:
        verify_source_hashes(snapshots)
    except SourcePromptDriftError as exc:
        return await _blocked_for_drift(source_target, str(exc))

    try:
        baseline = await source_target.read_all()
    except Exception as exc:
        return WritebackResult(
            status="failed",
            reason="write_error",
            error_message=f"failed to read source prompts before writeback: {exc}",
        )
    expected_baseline = {snapshot.field_name: snapshot.content for snapshot in snapshots}
    if baseline != expected_baseline:
        return await _blocked_for_drift(
            source_target,
            "source prompts changed after the initial hash check",
        )
    hashes_before = _field_hashes(baseline)

    # This synchronous check is intentionally adjacent to the path-backed
    # write. It narrows the compare/write window after the awaited read above.
    try:
        verify_source_hashes(snapshots)
    except SourcePromptDriftError as exc:
        return await _blocked_for_drift(source_target, str(exc))

    try:
        await source_target.write_all(candidate.prompts)
    except Exception as exc:
        restored = await _restore_and_verify(source_target, baseline)
        return WritebackResult(
            status="failed",
            reason="write_error",
            attempted=True,
            changed_fields=list(candidate.changed_fields),
            source_hashes_before=hashes_before,
            source_hashes_after=_field_hashes(restored),
            error_message=str(exc),
        )

    try:
        written = await source_target.read_all()
    except Exception as exc:
        restored = await _restore_and_verify(source_target, baseline)
        return WritebackResult(
            status="failed",
            reason="readback_mismatch",
            attempted=True,
            changed_fields=list(candidate.changed_fields),
            source_hashes_before=hashes_before,
            source_hashes_after=_field_hashes(restored),
            error_message=f"failed to read source prompts after writeback: {exc}",
        )
    if written != candidate.prompts:
        restored = await _restore_and_verify(source_target, baseline)
        return WritebackResult(
            status="failed",
            reason="readback_mismatch",
            attempted=True,
            changed_fields=list(candidate.changed_fields),
            source_hashes_before=hashes_before,
            source_hashes_after=_field_hashes(restored),
            error_message="source prompt readback did not match the accepted candidate",
        )

    return WritebackResult(
        status="written",
        reason="written",
        attempted=True,
        changed_fields=list(candidate.changed_fields),
        source_hashes_before=hashes_before,
        source_hashes_after=_field_hashes(written),
    )
