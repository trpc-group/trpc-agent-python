# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for codegen CLI and module entrypoints."""

import importlib
import json
import os
import runpy
from pathlib import Path
from typing import Any

import pytest
from trpc_agent_sdk.dsl.codegen._cli import generate_project
from trpc_agent_sdk.dsl.codegen._cli import main


def _build_cli_workflow_payload() -> dict[str, Any]:
    return {
        "name":
        "cli_flow",
        "description":
        "Workflow for CLI tests.",
        "version":
        "1.0",
        "start_node_id":
        "start",
        "nodes": [
            {
                "id": "start",
                "node_type": "builtin.start",
                "config": {}
            },
            {
                "id": "agent",
                "node_type": "builtin.llmagent",
                "config": {
                    "model_spec": {
                        "provider": "openai",
                        "model_name": "gpt-4o-mini",
                        "api_key": "env:OPENAI_API_KEY",
                    },
                    "instruction": "Echo input.",
                    "output_format": {
                        "type": "json",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "text": {
                                    "type": "string"
                                },
                            },
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                    },
                },
            },
            {
                "id": "end",
                "node_type": "builtin.end",
                "config": {
                    "expr": {
                        "expression": "input.output_parsed",
                        "format": "cel",
                    },
                },
            },
        ],
        "edges": [
            {
                "source": "start",
                "target": "agent"
            },
            {
                "source": "agent",
                "target": "end"
            },
        ],
        "conditional_edges": [],
    }


def _write_workflow(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class TestGenerateProject:
    """Tests for generate_project() file system behavior."""

    def test_generates_project_files_and_copies_workflow_json(self, tmp_path: Path):
        """generate_project should write rendered files and workflow.json copy."""
        workflow_path = _write_workflow(tmp_path / "workflow.json", _build_cli_workflow_payload())
        output_dir = tmp_path / "generated_project"

        written = generate_project(workflow_json_path=workflow_path, output_dir=output_dir, service="a2a")

        assert "workflow.json" in written
        assert written["workflow.json"] == output_dir / "workflow.json"
        assert (output_dir / "agent" / "agent.py").is_file()
        assert (output_dir / "trpc_main.py").is_file()
        assert (output_dir / "a2a_service.py").is_file()
        assert (output_dir / "trpc_python_client.yaml").is_file()
        assert (output_dir / "client.py").is_file()
        assert json.loads((output_dir / "workflow.json").read_text(encoding="utf-8")) == json.loads(
            workflow_path.read_text(encoding="utf-8"))

    def test_rejects_non_empty_output_dir_without_overwrite(self, tmp_path: Path):
        """generate_project should fail when output dir has content and overwrite is false."""
        workflow_path = _write_workflow(tmp_path / "workflow.json", _build_cli_workflow_payload())
        output_dir = tmp_path / "existing_dir"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "keep.txt").write_text("keep", encoding="utf-8")

        with pytest.raises(ValueError, match="Output directory is not empty"):
            generate_project(workflow_json_path=workflow_path, output_dir=output_dir)

    def test_allows_non_empty_output_dir_with_overwrite(self, tmp_path: Path):
        """generate_project should allow non-empty output dir when overwrite is true."""
        workflow_path = _write_workflow(tmp_path / "workflow.json", _build_cli_workflow_payload())
        output_dir = tmp_path / "existing_dir"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "keep.txt").write_text("keep", encoding="utf-8")

        written = generate_project(workflow_json_path=workflow_path, output_dir=output_dir, overwrite=True)
        assert "agent/agent.py" in written
        assert (output_dir / "agent" / "agent.py").is_file()


class TestMainEntrypoints:
    """Tests for CLI main() and module entry behavior."""

    def test_main_parses_args_calls_generate_and_prints_sorted_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ):
        """main should pass parsed args into generate_project and print sorted keys."""
        captured: dict[str, object] = {}

        def _fake_generate_project(
            workflow_json_path: str | Path,
            dsl_text: str | None = None,
            output_dir: str | Path | None = None,
            *,
            overwrite: bool = False,
            service: str | None = None,
        ) -> dict[str, Path]:
            captured["workflow_json_path"] = workflow_json_path
            captured["dsl_text"] = dsl_text
            captured["output_dir"] = output_dir
            captured["overwrite"] = overwrite
            captured["service"] = service
            return {
                "z.py": Path("/tmp/z.py"),
                "a.py": Path("/tmp/a.py"),
            }

        monkeypatch.setattr("trpc_agent_dsl.codegen._cli.generate_project", _fake_generate_project)

        workflow_path = tmp_path / "workflow.json"
        workflow_path.write_text("{}", encoding="utf-8")
        output_dir = tmp_path / "out_dir"

        status = main([str(workflow_path), "-o", str(output_dir), "--overwrite", "--service", "http"])
        output_lines = capsys.readouterr().out.strip().splitlines()

        assert status == 0
        assert captured == {
            "workflow_json_path": workflow_path.resolve(),
            "dsl_text": None,
            "output_dir": output_dir.resolve(),
            "overwrite": True,
            "service": "http",
        }
        assert output_lines[0] == "Code generation completed."
        assert output_lines[1].startswith("  - a.py:")
        assert output_lines[2].startswith("  - z.py:")

    def test_codegen_package_sets_default_report_flag_without_overriding_existing_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """Package __init__ should set default flag once and preserve explicit values."""
        monkeypatch.delenv("DISABLE_TRPC_AGENT_REPORT", raising=False)
        module = importlib.import_module("trpc_agent_dsl.codegen")
        importlib.reload(module)
        assert os.environ["DISABLE_TRPC_AGENT_REPORT"] == "true"

        monkeypatch.setenv("DISABLE_TRPC_AGENT_REPORT", "false")
        importlib.reload(module)
        assert os.environ["DISABLE_TRPC_AGENT_REPORT"] == "false"

    def test_python_m_entrypoint_exits_with_cli_status(self, monkeypatch: pytest.MonkeyPatch):
        """python -m trpc_agent_dsl.codegen should exit with _cli.main return code."""

        def _fake_main(argv: list[str] | None = None) -> int:
            assert argv is None
            return 7

        monkeypatch.setattr("trpc_agent_dsl.codegen._cli.main", _fake_main)
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("trpc_agent_dsl.codegen.__main__", run_name="__main__")

        assert exc_info.value.code == 7
