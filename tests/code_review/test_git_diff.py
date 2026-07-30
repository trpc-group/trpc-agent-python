"""Git collection and unified patch parsing tests."""

from pathlib import Path

import pytest

from examples.code_review_agent.code_review.git_diff import (
    GitDiffCollector,
    GitDiffError,
    parse_unified_patch,
)
from examples.code_review_agent.code_review.models import ChangeType, LineChangeType

from .conftest import git


def test_collects_changed_files_and_new_line_numbers(sample_repository: tuple[Path, str, str], ) -> None:
    repository, base, head = sample_repository

    effective_base, files = GitDiffCollector(repository).collect(base, head)

    assert effective_base == base
    assert [changed.path for changed in files] == ["README.md", "app.py"]
    app = next(changed for changed in files if changed.path == "app.py")
    assert app.change_type == ChangeType.MODIFIED
    assert app.language == "python"
    assert app.added_lines == 2
    assert app.deleted_lines == 0
    assert app.changed_new_lines == {2, 3}


def test_parse_patch_tracks_added_deleted_and_context_lines() -> None:
    patch = ("diff --git a/a.py b/a.py\n"
             "--- a/a.py\n"
             "+++ b/a.py\n"
             "@@ -1,3 +1,3 @@\n"
             " keep\n"
             "-old\n"
             "+new\n"
             " end\n")

    hunks, added, deleted, is_binary = parse_unified_patch(patch)

    assert (added, deleted, is_binary) == (1, 1, False)
    assert [line.change_type for line in hunks[0].lines] == [
        LineChangeType.CONTEXT,
        LineChangeType.DELETED,
        LineChangeType.ADDED,
        LineChangeType.CONTEXT,
    ]
    assert hunks[0].lines[2].new_line == 2


def test_rejects_revision_that_looks_like_an_option(sample_repository: tuple[Path, str, str], ) -> None:
    repository, _, _ = sample_repository
    collector = GitDiffCollector(repository)

    with pytest.raises(GitDiffError, match="Invalid Git revision"):
        collector.resolve_revision("--output=/tmp/surprise")


def test_collects_renamed_file_with_spaces(tmp_path: Path) -> None:
    repository = tmp_path / "rename-repository"
    repository.mkdir()
    git(repository, "init", "-q")
    git(repository, "config", "user.email", "review@example.com")
    git(repository, "config", "user.name", "Review Test")
    old_path = repository / "old name.py"
    old_path.write_text("value = 1\nkeep = 2\nother = 3\n", encoding="utf-8")
    git(repository, "add", "old name.py")
    git(repository, "commit", "-qm", "base")
    base = git(repository, "rev-parse", "HEAD")
    git(repository, "mv", "old name.py", "new name.py")
    (repository / "new name.py").write_text("value = 4\nkeep = 2\nother = 3\n", encoding="utf-8")
    git(repository, "commit", "-qam", "rename")
    head = git(repository, "rev-parse", "HEAD")

    _, files = GitDiffCollector(repository).collect(base, head)

    assert len(files) == 1
    assert files[0].change_type == ChangeType.RENAMED
    assert files[0].old_path == "old name.py"
    assert files[0].path == "new name.py"
    assert files[0].changed_new_lines == {1}
