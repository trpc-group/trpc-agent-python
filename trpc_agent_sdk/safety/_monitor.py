# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Small failure-isolated monitoring fan-out for safety observations."""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from collections.abc import Callable
from typing import Union

from ._models import SafetyHealthSignal
from ._models import SafetyObservation

MonitorEvent = Union[SafetyObservation, SafetyHealthSignal]


class MonitorSink(ABC):
    """A sink that receives immutable, already-redacted safety events."""

    @abstractmethod
    def emit(self, event: MonitorEvent) -> None:
        """Consume one event without changing its decision."""


class CallbackMonitorSink(MonitorSink):
    """Adapt a synchronous callback to the public monitor contract."""

    def __init__(self, callback: Callable[[MonitorEvent], None]):
        self._callback = callback

    def emit(self, event: MonitorEvent) -> None:
        self._callback(event)


class MonitorDispatcher:
    """Fan out events while isolating every sink failure."""

    def __init__(self, sinks: tuple[MonitorSink, ...] | list[MonitorSink] = ()):  # noqa: B008
        self._sinks = tuple(sinks)

    @property
    def sinks(self) -> tuple[MonitorSink, ...]:
        return self._sinks

    def emit(self, event: MonitorEvent) -> tuple[SafetyHealthSignal, ...]:
        signals: list[SafetyHealthSignal] = []
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception:  # pylint: disable=broad-except
                signals.append(
                    SafetyHealthSignal(
                        component=type(sink).__name__[:64],
                        failure_code="monitor_sink_failure",
                        message="A safety monitor sink failed; the decision was retained.",
                    ))
        for signal in signals:
            for sink in self._sinks:
                try:
                    sink.emit(signal)
                except Exception:  # pylint: disable=broad-except
                    continue
        return tuple(signals)
