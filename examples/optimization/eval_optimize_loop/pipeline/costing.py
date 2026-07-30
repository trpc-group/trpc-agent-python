"""Completed-call cost ledger and deterministic source merge."""

from __future__ import annotations

from .models import CostSource, CostSummary


class CostLedger:
    """Run-local ledger; callers record a source only after its call completes."""

    def __init__(self) -> None:
        self._sources: list[CostSource] = []

    def record(
        self,
        name: str,
        *,
        cost_usd: float | None,
        model_calls: int | None = None,
        metric_calls: int | None = None,
        token_usage: dict[str, int] | None = None,
    ) -> None:
        if any(source.name == name for source in self._sources):
            raise ValueError(f"cost source already recorded: {name!r}")
        self._sources.append(
            CostSource(
                name=name,
                cost_usd=cost_usd,
                model_calls=model_calls,
                metric_calls=metric_calls,
                token_usage=dict(token_usage or {}),
            ))

    def summary(self) -> CostSummary:
        known = all(source.cost_usd is not None for source in self._sources)
        total = sum(source.cost_usd or 0 for source in self._sources) if known else None
        return CostSummary(sources=tuple(self._sources), total_cost_usd=total)
