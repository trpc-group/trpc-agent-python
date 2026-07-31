"""Command-line boundary tests."""

from examples.optimization.eval_optimize_loop.run_pipeline import _format_cli_error
from examples.optimization.eval_optimize_loop.pipeline.schema import add_exception_note


def test_cli_error_output_is_bounded_and_redacted() -> None:
    error = RuntimeError(
        "Authorization: Bearer live-token; api_key=live-key; " + "context" * 1000)
    add_exception_note(error, "password=note-secret")

    rendered = _format_cli_error(error)

    assert len(rendered) <= 4000
    assert "live-token" not in rendered
    assert "live-key" not in rendered
    assert "note-secret" not in rendered
    assert rendered.count("[REDACTED]") == 3
