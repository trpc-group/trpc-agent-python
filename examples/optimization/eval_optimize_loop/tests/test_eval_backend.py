"""TraceBackend / LiveBackend 满足 EvalBackend Protocol."""

from pipeline._eval_backend import EvalBackend, TraceBackend, LiveBackend


def test_trace_backend_is_eval_backend():
    assert isinstance(TraceBackend(), EvalBackend)


def test_live_backend_is_eval_backend():
    assert isinstance(LiveBackend(), EvalBackend)
