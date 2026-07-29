#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Integration tests for A8 input acquisition and secure staging."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from code_review.config import ReviewConfig  # noqa: E402
from code_review.inputs import (  # noqa: E402
    FixturePayload,
    InputLimitError,
    InputValidationError,
    load_input,
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _repository_with_changes(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "review@example.invalid")
    _git(repo, "config", "user.name", "Code Review Test")
    (repo / "src").mkdir()
    (repo / "src" / "service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "src/service.py")
    _git(repo, "commit", "-m", "initial")
    (repo / "src" / "service.py").write_text("def run():\n    return eval(value)\n", encoding="utf-8")
    return repo


def test_diff_file_is_changed_line_input(tmp_path: Path) -> None:
    diff_file = tmp_path / "change.diff"
    diff_file.write_text(
        "\n".join(
            [
                "diff --git a/src/app.py b/src/app.py",
                "--- a/src/app.py",
                "+++ b/src/app.py",
                "@@ -1 +1 @@",
                "-value = 1",
                "+value = eval(payload)",
            ]
        ),
        encoding="utf-8",
    )

    result = load_input(diff_file=diff_file, input_root=tmp_path)

    assert result.change_set.source_kind == "diff_file"
    assert result.change_set.files[0].review_scope == "changed_lines"
    assert result.change_set.files[0].new_changed_lines == (1,)


def test_files_are_snapshot_full_file_inputs(tmp_path: Path) -> None:
    source = tmp_path / "src" / "snapshot.py"
    source.parent.mkdir()
    source.write_text("value = eval(payload)\n", encoding="utf-8")

    result = load_input(files=[Path("src/snapshot.py")], input_root=tmp_path)

    file_change = result.change_set.files[0]
    assert result.change_set.source_kind == "files"
    assert file_change.status == "snapshot"
    assert file_change.review_scope == "full_file"
    assert file_change.full_text.replace("\r\n", "\n") == "value = eval(payload)\n"


def test_fixture_preserves_declared_diff_or_full_file_payload() -> None:
    diff_payload = FixturePayload(
        payload_type="diff",
        diff_text="\n".join(
            [
                "diff --git a/src/app.py b/src/app.py",
                "--- a/src/app.py",
                "+++ b/src/app.py",
                "@@ -3 +3 @@",
                "-return old_value",
                "+return new_value",
            ]
        ),
    )
    files_payload = FixturePayload(
        payload_type="files",
        file_contents={"src/app.py": "return value\n"},
    )

    diff_result = load_input(fixture=diff_payload)
    files_result = load_input(fixture=files_payload)

    assert diff_result.change_set.source_kind == "fixture"
    assert diff_result.change_set.files[0].review_scope == "changed_lines"
    assert diff_result.change_set.files[0].new_changed_lines == (3,)
    assert files_result.change_set.source_kind == "fixture"
    assert files_result.change_set.files[0].status == "snapshot"
    assert files_result.change_set.files[0].review_scope == "full_file"


def test_repo_input_reads_tracked_full_text_and_untracked_text_files(tmp_path: Path) -> None:
    repo = _repository_with_changes(tmp_path)
    token = "ghp_" + "a" * 36
    (repo / ".env").write_text(f"TOKEN={token}\n", encoding="utf-8")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "ignored.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "asset.bin").write_bytes(b"\x00binary")

    result = load_input(repo_path=repo)

    changes = {file_change.normalized_path: file_change for file_change in result.change_set.files}
    assert changes["src/service.py"].status == "modified"
    assert changes["src/service.py"].full_text is not None
    assert result.change_set.source_kind == "repo_path"
    assert changes[".env"].status == "added"
    assert changes[".env"].review_scope == "full_file"
    assert ".venv/ignored.py" not in changes
    assert "asset.bin" not in changes
    assert result.warnings.count("input_binary_skipped") == 1
    assert token not in " ".join(result.warnings)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"diff_file": Path("one.diff"), "files": [Path("two.py")]},
        {"fixture": FixturePayload(payload_type="files", file_contents={"x.py": "x = 1\n"}), "repo_path": Path(".")},
    ],
)
def test_input_forms_are_mutually_exclusive(kwargs: dict[str, object]) -> None:
    with pytest.raises(InputValidationError, match="exactly_one_input_required"):
        load_input(**kwargs)


def test_files_reject_traversal_absolute_and_symlink_before_reading(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.py"
    outside.write_text("secret = 'outside'\n", encoding="utf-8")
    root = tmp_path / "root"
    root.mkdir()
    (root / "inside.py").write_text("value = 1\n", encoding="utf-8")
    junction = root / "junction"
    outside_dir = outside.parent / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "secret.py").write_text("secret = 'outside'\n", encoding="utf-8")
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside_dir)],
        check=True,
        capture_output=True,
        text=True,
    )

    for candidate in (Path("../outside.py"), outside, Path("junction/secret.py")):
        with pytest.raises(InputValidationError):
            load_input(files=[candidate], input_root=root)


def test_limits_are_rejected_before_input_content_is_loaded(tmp_path: Path) -> None:
    source = tmp_path / "large.py"
    source.write_text("value = 123456\n", encoding="utf-8")
    config = ReviewConfig(max_input_file_bytes=8, max_input_bytes=8)

    with pytest.raises(InputLimitError, match="input_file_too_large"):
        load_input(files=[Path("large.py")], input_root=tmp_path, config=config)

    (tmp_path / "one.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("value = 2\n", encoding="utf-8")
    total_config = ReviewConfig(max_input_file_bytes=16, max_input_bytes=19)
    with pytest.raises(InputLimitError, match="input_total_too_large"):
        load_input(
            files=[Path("one.py"), Path("two.py")],
            input_root=tmp_path,
            config=total_config,
        )


def test_fixture_name_requires_and_uses_a_typed_resolver() -> None:
    payload = FixturePayload(payload_type="files", file_contents={"src/example.py": "value = 1\n"})

    with pytest.raises(InputValidationError, match="fixture_resolver_required"):
        load_input(fixture="fixture-name")

    result = load_input(fixture="fixture-name", fixture_resolver=lambda _: payload)

    assert result.change_set.source_kind == "fixture"
    assert result.change_set.files[0].review_scope == "full_file"


def test_input_module_never_logs_raw_fixture_secret(caplog: pytest.LogCaptureFixture) -> None:
    token = "ghp_" + "b" * 36
    payload = FixturePayload(payload_type="files", file_contents={".env": f"TOKEN={token}\n"})

    result = load_input(fixture=payload)

    assert result.change_set.files[0].normalized_path == ".env"
    assert token not in caplog.text
