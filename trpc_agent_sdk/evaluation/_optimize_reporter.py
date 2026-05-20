# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Algorithm-agnostic progress sink for AgentOptimizer.

Defines :class:`OptimizeReporter` (the surface algorithms emit progress
events to) and three concrete backends:

  * :class:`_NullReporter`  drops every event (``verbose=0``).
  * :class:`_RichReporter`  Rich panel header, Live progress bar over
                            the budget, colourised round lines, closing
                            summary panel with per-metric comparison.
  * :class:`_AsciiReporter` plain-``print`` fallback for non-Rich
                            environments.

:class:`_SilentGepaLogger` is a ``gepa.LoggerProtocol``-compatible sink
the optimizer hands to gepa to keep library logs out of the reporter
timeline.

:func:`create_reporter` picks a backend by ``verbose`` level and ``rich``
availability.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import Any
from typing import Literal
from typing import Optional
from typing import Protocol
from typing import TextIO
from typing import runtime_checkable

if TYPE_CHECKING:
    from ._optimize_result import OptimizeResult

logger = logging.getLogger(__name__)

_GEPA_LOGGER_NAME = "trpc_agent_sdk.optimizer.gepa"

_MAX_TARGET_FIELDS_IN_HEADER = 8
_FIELD_NAME_DISPLAY_LIMIT = 40


@dataclass(frozen=True)
class RunHeader:
    """Static run context shown at run start.

    Attributes:
        algorithm: Registered algorithm name (e.g. ``gepa_reflective``).
        target_fields: Ordered ``(field_name, source_repr)`` pairs;
            ``source_repr`` is the file path for ``add_path`` fields or
            ``"<callback>"`` for ``add_callback`` fields.
        train_size: Training case count.
        val_size: Validation case count.
        metric_names: Display names of every reported metric.
        output_dir: Resolved artifact directory.
        budget_total: Configured metric-call budget (e.g.
            ``max_metric_calls``); ``None`` falls back to an
            indeterminate progress display.
    """

    algorithm: str
    target_fields: list[tuple[str, str]]
    train_size: int
    val_size: int
    metric_names: list[str]
    output_dir: str
    budget_total: Optional[int] = None


@dataclass(frozen=True)
class RoundView:
    """Single-round summary for one per-round line.

    Attributes:
        round: 1-based round index from the algorithm.
        kind: ``"reflective"`` (default) or ``"merge"``; unknown values
            render as ``"reflective"``.
        train_minibatch_size: ``M`` in ``train(M/N)``; 0 when the round
            skipped before sampling.
        train_size: ``N`` — full training set size.
        train_subsample_parent_score: Parent's score on the minibatch
            (None when no subsample produced).
        train_subsample_candidate_score: New candidate's score (None
            when not evaluated).
        val_pass_rate: Full validation pass rate when the candidate
            cleared the subsample gate (None otherwise).
        accepted: True iff the candidate joined the pool.
        skip_reason: Human-readable reason for skipped rounds.
        error_message: Set when the round ended in an error.
        duration_seconds: Wall-clock seconds.
        budget_used: Cumulative metric calls used (None when the
            algorithm doesn't track a budget).
        budget_total: Configured ``max_metric_calls`` (None means
            ``"auto"``).
        extras: Free-form algorithm-specific payload.
    """

    round: int
    kind: Literal["reflective", "merge"]
    train_minibatch_size: int
    train_size: int
    train_subsample_parent_score: Optional[float]
    train_subsample_candidate_score: Optional[float]
    val_pass_rate: Optional[float]
    accepted: bool
    skip_reason: Optional[str]
    error_message: Optional[str]
    duration_seconds: float
    budget_used: Optional[int]
    budget_total: Optional[int]
    extras: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class OptimizeReporter(Protocol):
    """Five-event surface every backend implements.

    Implementations swallow render errors; the facade also guards each
    call so a broken reporter never breaks optimization.
    """

    def run_started(self, header: RunHeader) -> None:
        ...

    def baseline_evaluated(
        self,
        pass_rate: float,
        metric_breakdown: dict[str, float],
        *,
        metric_thresholds: Optional[dict[str, float]] = None,
    ) -> None:
        ...

    def round_completed(self, view: RoundView) -> None:
        ...

    def run_finished(
        self,
        result: "OptimizeResult",
        *,
        output_dir: str,
        update_source: bool,
    ) -> None:
        ...

    def run_failed(
        self,
        *,
        baseline_prompts: dict[str, str],
        output_dir: str,
        error_message: str,
    ) -> None:
        ...


class _NullReporter:
    """No-op reporter used when ``verbose=0``."""

    def run_started(self, header: RunHeader) -> None:
        return None

    def baseline_evaluated(
        self,
        pass_rate: float,
        metric_breakdown: dict[str, float],
        *,
        metric_thresholds: Optional[dict[str, float]] = None,
    ) -> None:
        return None

    def round_completed(self, view: RoundView) -> None:
        return None

    def run_finished(
        self,
        result: "OptimizeResult",
        *,
        output_dir: str,
        update_source: bool,
    ) -> None:
        return None

    def run_failed(
        self,
        *,
        baseline_prompts: dict[str, str],
        output_dir: str,
        error_message: str,
    ) -> None:
        return None


def _truncate(text: str, limit: int) -> str:
    """Return ``text`` shortened to at most ``limit`` characters with ellipsis."""
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 3)] + "..."


def _format_source(source_repr: str) -> str:
    """Compact a target source for display in the run header.

    File-backed sources collapse to their basename (full path remains in
    ``config.snapshot.json`` / ``result.json``); callback sources keep their
    sentinel ``<callback>`` form.
    """
    if source_repr == "<callback>":
        return source_repr
    return os.path.basename(source_repr) or source_repr


def _format_sample_score_segment(view: RoundView, *, ascii_only: bool) -> str:
    """Render the ``sample score parent → candidate`` segment, or empty when absent."""
    parent = view.train_subsample_parent_score
    candidate = view.train_subsample_candidate_score
    if parent is None and candidate is None:
        return ""
    arrow = "->" if ascii_only else "→"
    if parent is None:
        return f"sample score   {candidate:.2f}"
    if candidate is None:
        return f"sample score {parent:.2f}"
    return f"sample score {parent:.2f} {arrow} {candidate:.2f}"


def _format_evaluations_segment(view: RoundView) -> str:
    """Render the trailing ``evaluations used/total`` segment, or empty when not tracked."""
    if view.budget_used is None:
        return ""
    total = "auto" if view.budget_total is None else str(view.budget_total)
    return f"evaluations {view.budget_used}/{total}"


def _round_marker(view: RoundView, *, ascii_only: bool) -> str:
    """Return the leading marker glyph for a round line.

    Glyph → meaning mapping (kept identical between ASCII and Rich):

      * ``✓`` accepted        — candidate beat the current best on valset.
      * ``○`` explored        — full valset evaluation ran but did not improve.
      * ``·`` skipped         — subsample gate / no-proposal / cache hit etc.
      * ``↻`` merge           — gepa system-aware merge round.
      * ``✗`` error           — round ended in an algorithm error.
    """
    if view.error_message:
        return "x" if ascii_only else "✗"
    if view.skip_reason:
        return "." if ascii_only else "·"
    if view.kind == "merge":
        return "~" if ascii_only else "↻"
    if view.accepted:
        return "OK" if ascii_only else "✓"
    return "-" if ascii_only else "○"


def _round_status_word(view: RoundView) -> str:
    """Return the textual status label rendered next to the round marker."""
    if view.error_message:
        return "error"
    if view.skip_reason:
        return "skipped"
    if view.kind == "merge":
        return "merged" if view.accepted else "merge"
    if view.accepted:
        return "accepted"
    return "explored"


def _format_stop_reason_text(stop_reason: Optional[str]) -> Optional[str]:
    """Translate ``OptimizeResult.stop_reason`` into the reporter row text.

    Returns ``None`` when no row should be emitted (i.e. the run errored
    before any stopper could classify a reason).
    """
    if stop_reason is None:
        return None
    text_by_reason = {
        "required_metrics_passing": "required metrics met thresholds",
        "budget_exhausted": "budget exhausted (max_metric_calls reached)",
        "no_improvement": "no improvement for the configured number of rounds",
        "timeout": "timeout reached",
        "score_threshold": "score threshold reached",
        "max_candidate_proposals": "max candidate proposals reached",
        "max_tracked_candidates": "max tracked candidates reached",
        "user_requested_stop": "user requested stop (optimize.stop touched)",
        "completed": "completed (no stopper triggered)",
    }
    return text_by_reason.get(stop_reason, stop_reason)


def _round_legend_lines(*, ascii_only: bool) -> list[str]:
    """Return the static legend block describing round-line semantics.

    Printed once between header and baseline so users can decode every
    subsequent round line without scrolling back.
    """
    arrow = "->" if ascii_only else "→"
    accepted = "OK" if ascii_only else "✓"
    explored = "-" if ascii_only else "○"
    skipped = "." if ascii_only else "·"
    merge = "~" if ascii_only else "↻"
    error = "x" if ascii_only else "✗"
    return [
        "Round line legend:",
        f"  format   : <mark> round N   <status>   train sample M/N   "
        f"sample score parent {arrow} candidate   "
        f"valset pass_rate Z   evaluations used/total   duration",
        f"  status   : {accepted} accepted   {explored} explored   "
        f"{skipped} skipped   {merge} merge   {error} error",
        "  train    : a minibatch of M cases sampled from the N-case training set "
        "for the reflective step.",
        "  sample   : parent vs new candidate score on that minibatch "
        "(skip gate decides whether to run valset).",
        "  valset   : pass_rate over the full validation set when the candidate "
        "cleared the skip gate.",
        "  budget   : evaluations used / configured budget (metric calls).",
    ]


def _improvement_arrow(delta: float, *, ascii_only: bool) -> str:
    """Return the directional arrow for a pass-rate delta."""
    if delta > 0:
        return "^" if ascii_only else "▲"
    if delta < 0:
        return "v" if ascii_only else "▼"
    return "="


def _format_improvement_label(delta: float) -> str:
    """Return a textual label describing the improvement direction."""
    if delta > 0:
        return "improved"
    if delta < 0:
        return "regressed"
    return "no improvement"


def _format_round_line(view: RoundView, *, ascii_only: bool) -> str:
    """Render a single-line per-round summary in ASCII form.

    Layout: ``<mark> round N  <status>   train sample M/N   sample score X -> Y
    <valset/skip/error segment>   evaluations U/T   <duration>``. Segments
    that do not apply to the current round (e.g. ``sample score`` for skipped
    rounds without subsample data) are omitted.
    """
    marker = _round_marker(view, ascii_only=ascii_only)
    status_word = _round_status_word(view)
    head = f"{marker} round {view.round}  {status_word}"

    segments: list[str] = []
    if view.train_minibatch_size > 0:
        segments.append(f"train sample {view.train_minibatch_size}/{view.train_size}")
    sample = _format_sample_score_segment(view, ascii_only=ascii_only)
    if sample:
        segments.append(sample)

    if view.error_message:
        segments.append(f"message: {view.error_message}")
    elif view.skip_reason:
        segments.append(f"reason: {view.skip_reason}")
    elif view.val_pass_rate is not None:
        segments.append(f"valset pass_rate {view.val_pass_rate:.4f}")

    evaluations = _format_evaluations_segment(view)
    if evaluations:
        segments.append(evaluations)

    body = "   ".join(segments)
    tail = f"  {view.duration_seconds:.1f}s"
    return f"{head}   {body}{tail}"


def _ordered_metric_keys(*breakdowns: dict[str, float], extra: Optional[list[str]] = None) -> list[str]:
    """Stable union of metric keys across baseline/best breakdowns and an
    optional ``extra`` ordering hint."""
    seen: dict[str, None] = {}
    if extra:
        for name in extra:
            seen.setdefault(name, None)
    for breakdown in breakdowns:
        for name in breakdown.keys():
            seen.setdefault(name, None)
    return list(seen.keys())


def _format_score(value: Optional[float]) -> str:
    """Return a fixed-width formatted metric score, or ``-`` when missing."""
    if value is None:
        return "  -   "
    return f"{value:.4f}"


def _format_delta(value: float, *, ascii_only: bool) -> tuple[str, str]:
    """Return a ``(arrow, text)`` pair describing a per-metric improvement."""
    arrow = _improvement_arrow(value, ascii_only=ascii_only)
    sign = "+" if value >= 0 else ""
    return arrow, f"{sign}{value:.4f}"


def _baseline_metric_status(
    score: Optional[float],
    threshold: Optional[float],
    *,
    ascii_only: bool,
) -> str:
    """Return ``PASS`` / ``FAIL`` (or ``-``) based on whether ``score`` cleared the threshold.

    Mirrors evaluator semantics (``PASSED if score >= threshold``) so the
    reporter never disagrees with the evaluator's own PASS / FAIL decision.
    """
    if score is None or threshold is None:
        return "  -  "
    if score >= threshold:
        return "PASS" if ascii_only else "PASS"
    return "FAIL" if ascii_only else "FAIL"


class _AsciiReporter:
    """Dependency-free reporter used as fallback for non-Rich environments.

    Renders every event as ordered plain text via ``print``; safe for log
    files and CI pipes. Falls back to ASCII glyphs when the stream encoding
    cannot represent the Unicode marker set.
    """

    def __init__(self, *, stream: TextIO = sys.stdout, verbose: int = 1) -> None:
        self._stream = stream
        self._verbose = verbose
        self._ascii_only = self._detect_ascii_only()

    def _detect_ascii_only(self) -> bool:
        """Return True when the stream encoding cannot render Unicode glyphs."""
        encoding = getattr(self._stream, "encoding", None) or sys.getdefaultencoding()
        try:
            "✓✗·↻▲▼○".encode(encoding)
        except (LookupError, UnicodeEncodeError):
            return True
        return False

    def run_started(self, header: RunHeader) -> None:
        lines = [
            "",
            "=" * 80,
            f"  AgentOptimizer  ·  {header.algorithm}",
            "=" * 80,
            self._format_targets_line(header.target_fields),
        ]
        for name, src in header.target_fields[:_MAX_TARGET_FIELDS_IN_HEADER]:
            display_name = _truncate(name, _FIELD_NAME_DISPLAY_LIMIT)
            lines.append(f"    - {display_name:<40s}  ({_format_source(src)})")
        if len(header.target_fields) > _MAX_TARGET_FIELDS_IN_HEADER:
            extra = len(header.target_fields) - _MAX_TARGET_FIELDS_IN_HEADER
            lines.append(f"    ... and {extra} more")
        lines.append(f"  train/val   : {header.train_size} / {header.val_size} cases")
        lines.append(f"  metrics     : {len(header.metric_names)} configured")
        for name in header.metric_names:
            lines.append(f"    - {name}")
        if header.budget_total is not None:
            lines.append(f"  budget      : {header.budget_total} metric calls")
        else:
            lines.append("  budget      : auto (no explicit cap)")
        lines.append(f"  output_dir  : {header.output_dir}")
        lines.append("-" * 80)
        lines.append("")
        lines.extend(_round_legend_lines(ascii_only=self._ascii_only))
        lines.append("")
        self._writelines(lines)

    @staticmethod
    def _format_targets_line(target_fields: list[tuple[str, str]]) -> str:
        if len(target_fields) == 1:
            return "  target      : 1 field"
        return f"  targets     : {len(target_fields)} fields"

    def baseline_evaluated(
        self,
        pass_rate: float,
        metric_breakdown: dict[str, float],
        *,
        metric_thresholds: Optional[dict[str, float]] = None,
    ) -> None:
        thresholds = metric_thresholds or {}
        lines = [f"baseline pass_rate = {pass_rate:.4f}"]
        keys = _ordered_metric_keys(metric_breakdown, extra=list(thresholds.keys()))
        if keys:
            lines.append("  per-metric (threshold | score | status):")
            for name in keys:
                score = metric_breakdown.get(name)
                threshold = thresholds.get(name)
                status = _baseline_metric_status(score, threshold, ascii_only=self._ascii_only)
                threshold_str = (f"{threshold:.4f}" if threshold is not None else "  -   ")
                score_str = _format_score(score)
                lines.append(f"    - {name:<40s}  threshold {threshold_str}   "
                             f"{score_str}   {status}")
        lines.append("")
        self._writelines(lines)

    def round_completed(self, view: RoundView) -> None:
        self._writelines([_format_round_line(view, ascii_only=self._ascii_only)])

    def run_finished(
        self,
        result: "OptimizeResult",
        *,
        output_dir: str,
        update_source: bool,
    ) -> None:
        self._writelines([""])
        self._writelines(self._build_summary_lines(
            result=result,
            output_dir=output_dir,
            update_source=update_source,
        ))

    def run_failed(
        self,
        *,
        baseline_prompts: dict[str, str],
        output_dir: str,
        error_message: str,
    ) -> None:
        self._writelines([
            "",
            "=" * 80,
            "  Optimization FAILED",
            "=" * 80,
            f"  error      : {error_message}",
            f"  output_dir : {output_dir}",
            f"  baseline preserved at {os.path.join(output_dir, 'baseline_prompts')}",
            "=" * 80,
            "",
        ])

    def _build_summary_lines(
        self,
        *,
        result: "OptimizeResult",
        output_dir: str,
        update_source: bool,
    ) -> list[str]:
        """Return the multi-line summary block printed at run finish."""
        arrow = _improvement_arrow(result.pass_rate_improvement, ascii_only=self._ascii_only)
        label = _format_improvement_label(result.pass_rate_improvement)
        accepted = sum(1 for r in result.rounds if r.accepted)
        sign = "+" if result.pass_rate_improvement >= 0 else ""
        rate_line = (f"  pass_rate   :  {result.baseline_pass_rate:.4f} -> {result.best_pass_rate:.4f}"
                     f"   {arrow} {sign}{result.pass_rate_improvement:.4f}   ({label})")
        lines = [
            "=" * 80,
            f"  Optimization complete  ·  {result.status}",
            "=" * 80,
            rate_line,
            f"  rounds      :  {accepted} accepted / {result.total_rounds} total",
            f"  duration    :  {result.duration_seconds:.2f}s",
        ]
        stop_text = _format_stop_reason_text(result.stop_reason)
        if stop_text is not None:
            lines.append(f"  stopped by  :  {stop_text}")
        if result.status != "SUCCEEDED" and result.error_message:
            lines.append(f"  error       :  {result.error_message}")
        metric_keys = _ordered_metric_keys(
            result.baseline_metric_breakdown,
            result.best_metric_breakdown,
            extra=list(result.metric_thresholds.keys()),
        )
        if metric_keys:
            lines.append("  per-metric  : threshold | baseline -> best | delta | status")
            for name in metric_keys:
                base = result.baseline_metric_breakdown.get(name)
                best = result.best_metric_breakdown.get(name)
                threshold = result.metric_thresholds.get(name)
                delta = (best or 0.0) - (base or 0.0)
                d_arrow, d_text = _format_delta(delta, ascii_only=self._ascii_only)
                base_str = _format_score(base)
                best_str = _format_score(best)
                threshold_str = (f"{threshold:.4f}" if threshold is not None else "  -   ")
                status = _baseline_metric_status(best, threshold, ascii_only=self._ascii_only)
                lines.append(f"    - {name:<40s}  threshold {threshold_str}   "
                             f"{base_str} -> {best_str}   {d_arrow} {d_text}   {status}")
        update_msg = self._format_update_source_line(result=result, output_dir=output_dir, update_source=update_source)
        if update_msg:
            lines.append(update_msg)
        lines.extend(self._format_artifacts_block(result=result, output_dir=output_dir))
        lines.append("=" * 80)
        lines.append("")
        return lines

    @staticmethod
    def _format_update_source_line(
        *,
        result: "OptimizeResult",
        output_dir: str,
        update_source: bool,
    ) -> Optional[str]:
        """Return the ``update_source`` row text or ``None`` to omit it."""
        if not update_source:
            best_dir = os.path.join(output_dir, "best_prompts")
            return f"  update_source: false  (best prompts at {best_dir}/)"
        if result.status == "SUCCEEDED":
            return "  update_source: true   (best written back to target sources)"
        return "  update_source: true   (run failed; sources restored from baseline)"

    @staticmethod
    def _format_artifacts_block(
        *,
        result: "OptimizeResult",
        output_dir: str,
    ) -> list[str]:
        """Return the artifact directory listing lines for the summary."""
        lines = ["  artifacts   :"]
        lines.append(f"    {output_dir}/")
        for name, content in result.best_prompts.items():
            display = _truncate(name, _FIELD_NAME_DISPLAY_LIMIT)
            lines.append(f"      best_prompts/{display}.md  ({len(content)} chars)")
        lines.append("      result.json   summary.txt   rounds/   run.log")
        return lines

    def _writelines(self, lines: list[str]) -> None:
        """Write a list of lines to the stream, swallowing render errors."""
        try:
            self._stream.write("\n".join(lines))
            self._stream.write("\n")
            try:
                self._stream.flush()
            except (AttributeError, ValueError):  # pragma: no cover - non-flushable buffers
                pass
        except Exception:  # pragma: no cover - never break optimization on render error
            logger.warning("AsciiReporter write failed", exc_info=True)


class _RichReporter:
    """Rich-backed reporter that degrades to plain output on non-TTY streams.

    Uses Rich panels for the header and the closing summary, a Live region
    with a progress bar over the configured metric-call budget for the
    duration of the run, and a single coloured line per round. The underlying
    ``rich.console.Console`` auto-detects whether the stream supports ANSI
    sequences.
    """

    def __init__(self, *, stream: TextIO = sys.stdout, verbose: int = 1) -> None:
        from rich.console import Console

        self._stream = stream
        self._verbose = verbose
        self._console = Console(
            file=stream,
            force_terminal=None,
            highlight=False,
            soft_wrap=False,
        )
        self._ascii = _AsciiReporter(stream=stream, verbose=verbose)
        self._progress = None
        self._budget_task = None
        self._budget_total: Optional[int] = None

    def run_started(self, header: RunHeader) -> None:
        from rich.panel import Panel
        from rich.table import Table
        from rich import box
        from rich.progress import (
            Progress,
            BarColumn,
            TextColumn,
            TimeElapsedColumn,
        )

        table = Table.grid(padding=(0, 2))
        table.add_column(no_wrap=True, style="dim")
        table.add_column(no_wrap=False)

        targets_label = ("target" if len(header.target_fields) == 1 else "targets")
        targets_value = ("1 field" if len(header.target_fields) == 1 else f"{len(header.target_fields)} fields")
        table.add_row(targets_label, targets_value)
        visible = header.target_fields[:_MAX_TARGET_FIELDS_IN_HEADER]
        for name, src in visible:
            display_name = _truncate(name, _FIELD_NAME_DISPLAY_LIMIT)
            table.add_row("", f"- {display_name}  [dim]({_format_source(src)})[/dim]")
        if len(header.target_fields) > len(visible):
            remainder = len(header.target_fields) - len(visible)
            table.add_row("", f"[dim]... and {remainder} more[/dim]")

        table.add_row("train/val", f"{header.train_size} / {header.val_size} cases")
        metric_count_label = ("metric" if len(header.metric_names) == 1 else f"metrics ({len(header.metric_names)})")
        table.add_row(metric_count_label, "")
        for name in header.metric_names:
            table.add_row("", f"- {name}")

        budget_text = (f"{header.budget_total} metric calls"
                       if header.budget_total is not None else "auto (no explicit cap)")
        table.add_row("budget", budget_text)
        table.add_row("output_dir", header.output_dir)

        panel = Panel(
            table,
            title=f"[bold]AgentOptimizer[/bold]  ·  [cyan]{header.algorithm}[/cyan]",
            box=box.ROUNDED,
            padding=(0, 1),
        )
        self._console.print(panel)
        self._console.print("")
        for line in _round_legend_lines(ascii_only=False):
            self._console.print(f"[dim]{line}[/dim]")
        self._console.print("")

        self._budget_total = header.budget_total
        # ``auto_refresh=False`` keeps the Live region quiescent between
        # explicit refresh calls — embedded IDE terminals and some CI
        # captures don't honour rich's cursor-up escapes, so the default
        # 10Hz auto-refresh would re-print the bar instead of erasing
        # it. Manual refresh on each ``round_completed`` keeps the
        # output bounded to one line per event.
        self._progress = Progress(
            TextColumn("[bold]progress[/bold]"),
            BarColumn(bar_width=None),
            TextColumn("{task.completed}/{task.total} metric calls"),
            TextColumn("•"),
            TimeElapsedColumn(),
            console=self._console,
            transient=False,
            expand=True,
            auto_refresh=False,
        )
        total = header.budget_total if header.budget_total is not None else 100
        self._budget_task = self._progress.add_task("budget", total=total)
        try:
            self._progress.start()
            self._progress.refresh()
        except Exception:  # pragma: no cover - Live region best-effort
            self._progress = None
            self._budget_task = None

    def baseline_evaluated(
        self,
        pass_rate: float,
        metric_breakdown: dict[str, float],
        *,
        metric_thresholds: Optional[dict[str, float]] = None,
    ) -> None:
        from rich.table import Table
        from rich import box

        thresholds = metric_thresholds or {}
        self._console.print(f"[bold]baseline pass_rate = {pass_rate:.4f}[/bold]")
        keys = _ordered_metric_keys(metric_breakdown, extra=list(thresholds.keys()))
        if keys:
            t = Table(box=box.SIMPLE, show_header=True, header_style="dim")
            t.add_column("metric", no_wrap=True)
            t.add_column("threshold", justify="right")
            t.add_column("baseline", justify="right")
            t.add_column("status", justify="right")
            for name in keys:
                score = metric_breakdown.get(name)
                threshold = thresholds.get(name)
                threshold_str = (f"{threshold:.4f}" if threshold is not None else "-")
                score_str = (f"{score:.4f}" if score is not None else "-")
                status = _baseline_metric_status(score, threshold, ascii_only=False)
                color = ("green" if status == "PASS" else "red" if status == "FAIL" else "dim")
                t.add_row(
                    name,
                    threshold_str,
                    score_str,
                    f"[{color}]{status}[/{color}]",
                )
            self._console.print(t)
        self._console.print("")

    def round_completed(self, view: RoundView) -> None:
        if self._progress is not None and view.budget_used is not None:
            try:
                if self._budget_total is None:
                    # When no upper bound was set, grow the bar with usage.
                    self._progress.update(
                        self._budget_task,
                        completed=view.budget_used,
                        total=max(view.budget_used, 1),
                    )
                else:
                    self._progress.update(
                        self._budget_task,
                        completed=min(view.budget_used, self._budget_total),
                    )
                # Explicit refresh because ``auto_refresh=False`` keeps
                # the Live region quiescent between events.
                self._progress.refresh()
            except Exception:  # pragma: no cover
                pass

        marker = _round_marker(view, ascii_only=False)
        status_word = _round_status_word(view)
        style = self._round_style(view)
        head = (f"[{style}]{marker} round {view.round}  {status_word}[/{style}]")

        segments: list[str] = []
        if view.train_minibatch_size > 0:
            segments.append(f"train sample {view.train_minibatch_size}/{view.train_size}")
        sample = _format_sample_score_segment(view, ascii_only=False)
        if sample:
            segments.append(sample)
        if view.error_message:
            segments.append(f"[red]message: {view.error_message}[/red]")
        elif view.skip_reason:
            segments.append(f"[dim]reason: {view.skip_reason}[/dim]")
        elif view.val_pass_rate is not None:
            segments.append(f"[green]valset pass_rate {view.val_pass_rate:.4f}[/green]")
        evaluations = _format_evaluations_segment(view)
        if evaluations:
            segments.append(f"[dim]{evaluations}[/dim]")
        body = "   ".join(segments)
        tail = f"  [dim]{view.duration_seconds:.1f}s[/dim]"
        self._console.print(f"{head}   {body}{tail}")

    @staticmethod
    def _round_style(view: RoundView) -> str:
        """Return the Rich style string for the round marker."""
        if view.error_message:
            return "bold red"
        if view.skip_reason:
            return "dim"
        if view.accepted:
            return "bold green"
        return "yellow"

    def _stop_progress(self) -> None:
        if self._progress is None:
            return
        try:
            self._progress.stop()
        except Exception:  # pragma: no cover
            pass
        self._progress = None
        self._budget_task = None

    def run_finished(
        self,
        result: "OptimizeResult",
        *,
        output_dir: str,
        update_source: bool,
    ) -> None:
        from rich.panel import Panel
        from rich.table import Table
        from rich import box

        self._stop_progress()

        accepted = sum(1 for r in result.rounds if r.accepted)
        sign = "+" if result.pass_rate_improvement >= 0 else ""
        arrow = _improvement_arrow(result.pass_rate_improvement, ascii_only=False)
        label = _format_improvement_label(result.pass_rate_improvement)
        delta_color = ("green"
                       if result.pass_rate_improvement > 0 else "red" if result.pass_rate_improvement < 0 else "dim")

        table = Table.grid(padding=(0, 2))
        table.add_column(no_wrap=True, style="dim")
        table.add_column(no_wrap=False)
        rate_value = (f"{result.baseline_pass_rate:.4f} -> [bold]{result.best_pass_rate:.4f}[/bold]   "
                      f"[{delta_color}]{arrow} {sign}{result.pass_rate_improvement:.4f}[/{delta_color}]   "
                      f"[{delta_color}]({label})[/{delta_color}]")
        table.add_row("pass_rate", rate_value)
        table.add_row("rounds", f"{accepted} accepted / {result.total_rounds} total")
        table.add_row("duration", f"{result.duration_seconds:.2f}s")
        stop_text = _format_stop_reason_text(result.stop_reason)
        if stop_text is not None:
            table.add_row("stopped by", stop_text)
        if result.status != "SUCCEEDED" and result.error_message:
            table.add_row("error", f"[red]{result.error_message}[/red]")
        update_msg = _AsciiReporter._format_update_source_line(result=result,
                                                               output_dir=output_dir,
                                                               update_source=update_source)
        if update_msg:
            table.add_row("update_source", update_msg.split(":", 1)[1].strip())
        table.add_row("artifacts", f"{output_dir}/")
        for name, content in result.best_prompts.items():
            display = _truncate(name, _FIELD_NAME_DISPLAY_LIMIT)
            table.add_row("", f"best_prompts/{display}.md  [dim]({len(content)} chars)[/dim]")
        table.add_row("", "result.json   summary.txt   rounds/   run.log")

        title_style = "bold green" if result.status == "SUCCEEDED" else "bold red"
        panel = Panel(
            table,
            title=f"[{title_style}]Optimization complete  ·  {result.status}[/{title_style}]",
            box=box.ROUNDED,
            padding=(0, 1),
        )
        self._console.print("")
        self._console.print(panel)

        metric_keys = _ordered_metric_keys(
            result.baseline_metric_breakdown,
            result.best_metric_breakdown,
            extra=list(result.metric_thresholds.keys()),
        )
        if metric_keys:
            mt = Table(
                title="per-metric scores",
                box=box.SIMPLE_HEAVY,
                show_header=True,
                header_style="bold",
                title_style="dim",
            )
            mt.add_column("metric", no_wrap=True)
            mt.add_column("threshold", justify="right")
            mt.add_column("baseline", justify="right")
            mt.add_column("best", justify="right")
            mt.add_column("delta", justify="right")
            mt.add_column("status", justify="right")
            for name in metric_keys:
                base = result.baseline_metric_breakdown.get(name)
                best = result.best_metric_breakdown.get(name)
                threshold = result.metric_thresholds.get(name)
                delta = (best or 0.0) - (base or 0.0)
                d_color = ("green" if delta > 0 else "red" if delta < 0 else "dim")
                d_arrow, d_text = _format_delta(delta, ascii_only=False)
                base_str = _format_score(base)
                best_str = _format_score(best)
                threshold_str = (f"{threshold:.4f}" if threshold is not None else "-")
                status = _baseline_metric_status(best, threshold, ascii_only=False)
                status_color = ("green" if status == "PASS" else "red" if status == "FAIL" else "dim")
                mt.add_row(
                    name,
                    threshold_str,
                    base_str,
                    best_str,
                    f"[{d_color}]{d_arrow} {d_text}[/{d_color}]",
                    f"[{status_color}]{status}[/{status_color}]",
                )
            self._console.print(mt)

    def run_failed(
        self,
        *,
        baseline_prompts: dict[str, str],
        output_dir: str,
        error_message: str,
    ) -> None:
        from rich.panel import Panel
        from rich import box

        self._stop_progress()

        body = (f"[red]error      :[/red] {error_message}\n"
                f"output_dir : {output_dir}\n"
                f"baseline preserved at {os.path.join(output_dir, 'baseline_prompts')}")
        panel = Panel(
            body,
            title="[bold red]Optimization FAILED[/bold red]",
            box=box.ROUNDED,
            padding=(0, 1),
        )
        self._console.print("")
        self._console.print(panel)


class _SilentGepaLogger:
    """gepa-LoggerProtocol-compatible sink used to suppress library logs.

    With ``verbose<=1`` every message is dropped; with ``verbose>=2`` messages
    are forwarded to the ``trpc_agent_sdk.optimizer.gepa`` logger at INFO
    level so callers can route them via the standard logging configuration.
    """

    def __init__(self, *, verbose: int) -> None:
        self._verbose = verbose
        self._target = logging.getLogger(_GEPA_LOGGER_NAME) if verbose >= 2 else None

    def log(self, message: str) -> None:
        if self._target is not None:
            self._target.info("%s", message)


def create_reporter(
    *,
    verbose: int = 1,
    stream: TextIO = sys.stdout,
) -> OptimizeReporter:
    """Pick the appropriate reporter backend.

    Resolution order: ``verbose == 0`` returns :class:`_NullReporter`;
    otherwise the factory attempts to import ``rich`` and returns
    :class:`_RichReporter` on success or :class:`_AsciiReporter` on failure.
    Unknown ``verbose`` values are normalised to ``1``.
    """
    if verbose == 0:
        return _NullReporter()
    if verbose not in (1, 2):
        verbose = 1
    try:
        import rich  # noqa: F401
    except ImportError:
        return _AsciiReporter(stream=stream, verbose=verbose)
    return _RichReporter(stream=stream, verbose=verbose)
