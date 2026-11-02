"""Module-based target loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from trpc_agent_bench.core._target import load_target


def _write_module(root: Path, module: str, source: str) -> None:
    parts = module.split(".")
    for index in range(1, len(parts)):
        package = root.joinpath(*parts[:index])
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    path = root.joinpath(*parts).with_suffix(".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_load_target_from_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_module(tmp_path, "target_pkg_a.my_target", "class Target: pass\nTARGET = Target()\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    target = load_target("target_pkg_a.my_target")

    assert type(target).__name__ == "Target"


def test_load_target_uses_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_module(tmp_path, "target_pkg_b.env_target", "TARGET = object()\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("BENCH_TARGET", "target_pkg_b.env_target")

    assert load_target() is not None


def test_load_target_requires_target_attribute(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_module(tmp_path, "target_pkg_c.no_target", "X = 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(RuntimeError, match="does not define TARGET"):
        load_target("target_pkg_c.no_target")


def test_load_target_requires_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCH_TARGET", raising=False)

    with pytest.raises(RuntimeError, match="BENCH_TARGET is not set"):
        load_target()
