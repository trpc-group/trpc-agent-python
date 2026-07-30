"""Prompt context filtering and budget tests."""

from examples.code_review_agent.code_review.context_builder import (
    ContextBudget,
    build_review_context,
)
from examples.code_review_agent.code_review.models import ChangedFile, ChangeType


def _file(path: str, patch: str, *, binary: bool = False) -> ChangedFile:
    return ChangedFile(
        path=path,
        change_type=ChangeType.MODIFIED,
        language="python",
        patch=patch,
        is_binary=binary,
    )


def test_skips_binary_and_generated_paths() -> None:
    context = build_review_context([
        _file("src/app.py", "+print('ok')\n"),
        _file("vendor/lib.py", "+ignored\n"),
        _file("image.png", "", binary=True),
    ])

    assert context.included_files == ("src/app.py", )
    assert set(context.skipped_files) == {"vendor/lib.py", "image.png"}
    assert "### FILE: src/app.py" in context.text
    assert "ADDED LINE MAP" in context.text


def test_truncates_each_patch_without_exceeding_total_budget() -> None:
    changed_file = _file("src/app.py", "+" + ("x" * 500))
    context = build_review_context(
        [changed_file],
        ContextBudget(max_files=2, max_patch_chars_per_file=80, max_total_chars=500),
    )

    assert context.included_files == ("src/app.py", )
    assert context.truncated_files == ("src/app.py", )
    assert "[PATCH TRUNCATED]" in context.text
    assert len(changed_file.patch) <= 80
    assert len(context.text) <= 500
