# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Skill dependency inspection helpers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from typing import Optional

from ..config import load_config
from ._skill_loader import ClawSkillLoader

PROFILE_PDF = "pdf"
PROFILE_OFFICE = "office"
PROFILE_AUDIO = "audio"
PROFILE_VIDEO = "video"
PROFILE_IMAGE = "image"
PROFILE_OCR = "ocr"
PROFILE_COMMON_FILE_TOOLS = "common-file-tools"

_DEFAULT_PROFILES = [PROFILE_COMMON_FILE_TOOLS]

_PROFILE_CATALOG: dict[str, dict[str, Any]] = {
    PROFILE_PDF: {
        "description":
        "PDF readers, text extraction, and Python fallbacks.",
        "requires": {
            "bins": ["pdftotext", "pdfinfo"]
        },
        "install_actions": [
            {
                "kind": "brew",
                "package": "poppler"
            },
            {
                "kind": "apt",
                "package": "poppler-utils"
            },
            {
                "kind": "dnf",
                "package": "poppler-utils"
            },
            {
                "kind": "yum",
                "package": "poppler-utils"
            },
        ],
    },
    PROFILE_OFFICE: {
        "description": "Spreadsheet, Word, and slide parsing helpers.",
        "requires": {},
        "install_actions": [],
    },
    PROFILE_AUDIO: {
        "description":
        "Audio transcoding and inspection tools.",
        "requires": {
            "bins": ["ffmpeg", "ffprobe"]
        },
        "install_actions": [
            {
                "kind": "brew",
                "package": "ffmpeg"
            },
            {
                "kind": "apt",
                "package": "ffmpeg"
            },
            {
                "kind": "dnf",
                "package": "ffmpeg"
            },
            {
                "kind": "yum",
                "package": "ffmpeg"
            },
        ],
    },
    PROFILE_VIDEO: {
        "description":
        "Video frame extraction and transcoding tools.",
        "requires": {
            "bins": ["ffmpeg", "ffprobe"]
        },
        "install_actions": [
            {
                "kind": "brew",
                "package": "ffmpeg"
            },
            {
                "kind": "apt",
                "package": "ffmpeg"
            },
            {
                "kind": "dnf",
                "package": "ffmpeg"
            },
            {
                "kind": "yum",
                "package": "ffmpeg"
            },
        ],
    },
    PROFILE_IMAGE: {
        "description":
        "Common image conversion and manipulation tools.",
        "requires": {
            "any_bins": ["magick", "convert"]
        },
        "install_actions": [
            {
                "kind": "brew",
                "package": "imagemagick"
            },
            {
                "kind": "apt",
                "package": "imagemagick"
            },
            {
                "kind": "dnf",
                "package": "ImageMagick"
            },
            {
                "kind": "yum",
                "package": "ImageMagick"
            },
        ],
    },
    PROFILE_OCR: {
        "description":
        "OCR utilities for scanned images and documents.",
        "requires": {
            "bins": ["tesseract"]
        },
        "install_actions": [
            {
                "kind": "brew",
                "package": "tesseract"
            },
            {
                "kind": "apt",
                "package": "tesseract-ocr"
            },
            {
                "kind": "dnf",
                "package": "tesseract"
            },
            {
                "kind": "yum",
                "package": "tesseract"
            },
        ],
    },
    PROFILE_COMMON_FILE_TOOLS: {
        "description": "Recommended default toolchain for common file work.",
        "expands": [
            PROFILE_PDF,
            PROFILE_OFFICE,
            PROFILE_AUDIO,
            PROFILE_VIDEO,
            PROFILE_IMAGE,
            PROFILE_OCR,
        ],
        "requires": {},
        "install_actions": [],
    },
}


def _split_csv(raw: str) -> list[str]:
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        name = part.strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out


def _ensure_workspace_layout(workspace: Path) -> None:
    """Create minimal workspace folders needed by load_config side effects."""
    root = workspace.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for sub in ("sessions", "soul", "user", "tool", "agent", "memory", "skills"):
        (root / sub).mkdir(parents=True, exist_ok=True)


def write_test_config_file(config_path: Path, workspace: Path) -> Path:
    """Write a minimal config file for dependency tests.

    This helper intentionally only sets workspace-related fields so callers can
    inject more settings later.

    Example:
        config_path = ``/tmp/my_deps_test/config.json``

        The generated file content is:
        {
          "agent": {
            "workspace": "/tmp/my_deps_test/workspace"
          }
        }
    """
    cfg_path = config_path.expanduser().resolve()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "agent": {
            "workspace": str(workspace.expanduser().resolve()),
        }
    }
    cfg_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg_path


def _normalize_skill_names(raw: str) -> list[str]:
    return _split_csv(raw)


def _detect_package_manager() -> str:
    for name in ("brew", "apt-get", "dnf", "yum"):
        if shutil.which(name):
            if name == "apt-get":
                return "apt"
            return name
    return ""


def _command_for_action(action: dict[str, str]) -> str:
    kind = str(action.get("kind", "")).strip().lower()
    package = str(action.get("package", "")).strip()
    if not kind or not package:
        return ""
    if kind == "brew":
        return f"brew install {package}"
    if kind == "apt":
        return f"apt-get install -y {package}"
    if kind == "dnf":
        return f"dnf install -y {package}"
    if kind == "yum":
        return f"yum install -y {package}"
    if kind == "pip":
        return f"python -m pip install {package}"
    if kind == "uv":
        return f"uv pip install {package}"
    return ""


def _pick_install_command(actions: list[dict[str, str]]) -> str:
    if not actions:
        return ""
    pkg_mgr = _detect_package_manager()
    if pkg_mgr:
        for action in actions:
            if str(action.get("kind", "")).strip().lower() == pkg_mgr:
                return _command_for_action(action)
    for action in actions:
        command = _command_for_action(action)
        if command:
            return command
    return ""


def _resolve_profiles(names: list[str]) -> list[str]:
    selected = [item.lower() for item in names if item.strip()]
    if not selected:
        selected = list(_DEFAULT_PROFILES)
    out: list[str] = []
    seen: set[str] = set()

    def append_profile(name: str) -> None:
        key = name.strip().lower()
        if not key:
            return
        if key not in _PROFILE_CATALOG:
            raise ValueError(f"unknown dependency profile: {key}")
        if key in seen:
            return
        seen.add(key)
        for child in _PROFILE_CATALOG[key].get("expands", []):
            append_profile(str(child))
        # Aggregate profile: no own requires/install, only expansion.
        req = _PROFILE_CATALOG[key].get("requires", {}) or {}
        install_actions = _PROFILE_CATALOG[key].get("install_actions", []) or []
        if not req and not install_actions and _PROFILE_CATALOG[key].get("expands"):
            return
        out.append(key)

    for name in selected:
        append_profile(name)
    return out


def _sources_for_profiles(profile_names: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    resolved = _resolve_profiles(profile_names)
    for name in resolved:
        spec = _PROFILE_CATALOG[name]
        out.append({
            "name": name,
            "description": spec.get("description", ""),
            "requires": spec.get("requires", {}) or {},
            "install": _pick_install_command(spec.get("install_actions", []) or []),
        })
    return out


def _merge_sources(sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source in sources:
        name = str(source.get("name", "")).strip()
        if not name:
            continue
        normalized.append({**source, "name": name})
    normalized.sort(key=lambda item: str(item.get("name", "")))
    return normalized


def _normalize_any_bins(value: object) -> list[list[str]]:
    if not isinstance(value, list):
        return []
    if value and all(isinstance(item, str) for item in value):
        group = [str(item).strip() for item in value if str(item).strip()]
        return [group] if group else []
    groups: list[list[str]] = []
    for item in value:
        if isinstance(item, list):
            group = [str(x).strip() for x in item if str(x).strip()]
            if group:
                groups.append(group)
    return groups


def _normalize_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _has_config_key(available: set[str], want: str) -> bool:
    if want in available:
        return True
    prefix = f"{want}."
    return any(key.startswith(prefix) for key in available)


def _inspect_source(source: dict[str, Any], available_config_keys: set[str]) -> dict[str, Any]:
    requires = source.get("requires", {}) if isinstance(source.get("requires"), dict) else {}
    bins = _normalize_list(requires.get("bins", []))
    any_bins = _normalize_any_bins(requires.get("any_bins", []))
    envs = _normalize_list(requires.get("env", []))
    config_keys = [item.lower() for item in _normalize_list(requires.get("config", []))]

    missing_bins = [name for name in bins if not shutil.which(name)]
    missing_any_bins = [group for group in any_bins if not any(shutil.which(name) for name in group)]
    missing_env = [name for name in envs if not os.environ.get(name, "").strip()]
    missing_config = [key for key in config_keys if not _has_config_key(available_config_keys, key)]

    has_missing = bool(missing_bins or missing_any_bins or missing_env or missing_config)
    install_value = source.get("install", "")
    if isinstance(install_value, list):
        install_cmd = _pick_install_command(install_value)
    else:
        install_cmd = str(install_value).strip()
    return {
        "name": source.get("name", ""),
        "description": source.get("description", ""),
        "install": install_cmd,
        "requires": {
            "bins": bins,
            "any_bins": any_bins,
            "env": envs,
            "config": config_keys,
        },
        "missing": {
            "bins": missing_bins,
            "any_bins": missing_any_bins,
            "env": missing_env,
            "config": missing_config,
        },
        "ok": not has_missing,
    }


def inspect_skill_dependencies(
    *,
    config_path: Optional[Path] = None,
    workspace: Optional[Path] = None,
    skills_raw: str = "",
    profiles_raw: str = "",
    state_dir: str = "",
    skills_root: str = "",
    skills_extra_dirs_raw: str = "",
    skills_allow_bundled_raw: str = "",
) -> dict[str, Any]:
    """Inspect dependency status and produce install plan suggestions.

    Parameters:
        config_path:
            Config file path (yaml/json). Example: ``/tmp/my_deps_test/config.json``.
        workspace:
            Workspace root used by OpenClaw runtime initialization.
            Example: ``/tmp/my_deps_test/workspace``.
        skills_raw:
            Comma-separated skill names. Example: ``"knot-skill-finder,another-skill"``.
        profiles_raw:
            Comma-separated dependency profile names.
            Supported names: ``pdf, office, audio, video, image, ocr, common-file-tools``.
        state_dir:
            Reserved parity parameter with Go CLI; currently informational in report.
        skills_root:
            Override skills root path.
        skills_extra_dirs_raw:
            Extra skills roots, comma-separated.
        skills_allow_bundled_raw:
            Bundled allowlist override, comma-separated.

    Name execution examples:
        1) By profile names only (default toolchain checks):
            inspect_skill_dependencies(
                config_path=Path("/tmp/my_deps_test/config.json"),
                workspace=Path("/tmp/my_deps_test/workspace"),
                profiles_raw="common-file-tools",
            )

        2) By skill names only (check selected skills):
            inspect_skill_dependencies(
                config_path=Path("/tmp/my_deps_test/config.json"),
                workspace=Path("/tmp/my_deps_test/workspace"),
                skills_raw="knot-skill-finder",
            )

        3) Profile + skill names together:
            inspect_skill_dependencies(
                config_path=Path("/tmp/my_deps_test/config.json"),
                workspace=Path("/tmp/my_deps_test/workspace"),
                profiles_raw="pdf,image",
                skills_raw="knot-skill-finder",
                skills_allow_bundled_raw="knot.skill.finder,knot-skill-finder",
            )

    Command-line examples (recommended usage):
        1) Profiles only:
            openclaw deps \\
              --config /tmp/my_deps_test/config.json \\
              --workspace /tmp/my_deps_test/workspace \\
              --profile common-file-tools

        2) Skills only:
            openclaw deps \\
              --config /tmp/my_deps_test/config.json \\
              --workspace /tmp/my_deps_test/workspace \\
              --skills knot-skill-finder

        3) Profiles + skills + path overrides:
            openclaw deps \\
              --config /tmp/my_deps_test/config.json \\
              --workspace /tmp/my_deps_test/workspace \\
              --profile pdf,image \\
              --skills knot-skill-finder \\
              --skills-root /path/to/skills \\
              --skills-extra-dirs /path/a,/path/b \\
              --skills-allow-bundled knot.skill.finder,knot-skill-finder

        4) JSON output + execute install plan:
            openclaw deps \\
              --config /tmp/my_deps_test/config.json \\
              --workspace /tmp/my_deps_test/workspace \\
              --profile common-file-tools \\
              --json \\
              --apply

    Notes:
        - Profile + skill source aggregation mirrors Go deps_cmd flow.
        - Python implementation uses a built-in profile catalog aligned with Go.
    """
    if workspace is not None:
        _ensure_workspace_layout(workspace)
    cfg = load_config(config_path)
    if workspace is not None:
        cfg.agent.workspace = str(workspace.expanduser().resolve())
        _ensure_workspace_layout(workspace)

    # Go-compatible runtime overrides.
    if skills_root.strip():
        cfg.skills.skill_roots = [skills_root.strip()]
    extra_roots = _split_csv(skills_extra_dirs_raw)
    if extra_roots:
        cfg.skills.skill_roots.extend(extra_roots)
    allow_override = _split_csv(skills_allow_bundled_raw)
    if allow_override:
        cfg.skills.allow_bundled = allow_override

    loader = ClawSkillLoader(config=cfg)
    selected = _normalize_skill_names(skills_raw)
    selected_profiles = _split_csv(profiles_raw)
    if not selected and not selected_profiles:
        selected_profiles = list(_DEFAULT_PROFILES)

    profile_sources = _sources_for_profiles(selected_profiles) if selected_profiles else []
    skill_sources = loader.dependency_sources(selected) if selected else []
    sources = _merge_sources(profile_sources + skill_sources)

    if selected:
        known = {str(item.get("name", "")).strip() for item in sources}
        unknown = [name for name in selected if name not in known]
        if unknown:
            raise ValueError(f"unknown skill: {', '.join(unknown)}")

    available_config_keys = {str(key).strip().lower() for key in cfg.skills.config_keys if str(key).strip()}
    inspected_sources = [_inspect_source(source, available_config_keys) for source in sources]

    missing_bins: list[str] = []
    missing_any_bins: list[list[str]] = []
    missing_env: list[str] = []
    missing_config: list[str] = []
    for item in inspected_sources:
        for name in item["missing"]["bins"]:
            if name not in missing_bins:
                missing_bins.append(name)
        for group in item["missing"]["any_bins"]:
            if group not in missing_any_bins:
                missing_any_bins.append(group)
        for name in item["missing"]["env"]:
            if name not in missing_env:
                missing_env.append(name)
        for key in item["missing"]["config"]:
            if key not in missing_config:
                missing_config.append(key)

    plan: list[dict[str, str]] = []
    for item in inspected_sources:
        install = str(item.get("install", "")).strip()
        if item.get("ok") or not install:
            continue
        plan.append({
            "skill": str(item.get("name", "")).strip(),
            "command": install,
        })

    return {
        "state_dir": state_dir.strip(),
        "selected_profiles": selected_profiles,
        "selected": selected,
        "sources": inspected_sources,
        "missing": {
            "bins": missing_bins,
            "any_bins": missing_any_bins,
            "env": missing_env,
            "config": missing_config,
        },
        "plan": plan,
        "has_missing": bool(missing_bins or missing_any_bins or missing_env or missing_config),
        "profile_sources_supported": True,
    }


def apply_dependency_plan(
    report: dict[str, Any],
    *,
    continue_on_error: bool = True,
) -> dict[str, Any]:
    """Execute install plan commands and return step results."""
    plan = report.get("plan", []) if isinstance(report.get("plan", []), list) else []
    steps: list[dict[str, Any]] = []
    had_failures = False
    for index, item in enumerate(plan, start=1):
        skill = str(item.get("skill", "")).strip()
        command = str(item.get("command", "")).strip()
        if not command:
            steps.append({
                "index": index,
                "skill": skill,
                "command": command,
                "status": "deferred",
                "exit_code": None,
                "stdout": "",
                "stderr": "empty install command",
            })
            continue
        result = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            check=False,
        )
        status = "applied" if result.returncode == 0 else "failed"
        steps.append({
            "index": index,
            "skill": skill,
            "command": command,
            "status": status,
            "exit_code": result.returncode,
            "stdout": (result.stdout or "").strip(),
            "stderr": (result.stderr or "").strip(),
        })
        if status == "failed":
            had_failures = True
            if not continue_on_error:
                break
    return {
        "steps": steps,
        "applied": [step for step in steps if step["status"] == "applied"],
        "deferred": [step for step in steps if step["status"] == "deferred"],
        "failed": [step for step in steps if step["status"] == "failed"],
        "has_failures": had_failures,
    }


def render_dependency_report(report: dict[str, Any]) -> str:
    """Render human-readable dependency inspection report."""
    lines: list[str] = []
    selected = report.get("selected", [])
    selected_profiles = report.get("selected_profiles", [])
    if selected_profiles:
        lines.append(f"Selected profiles: {', '.join(selected_profiles)}")
    if selected:
        lines.append(f"Selected skills: {', '.join(selected)}")
    else:
        lines.append("Selected skills: none")
    lines.append("")
    lines.append("Dependency status:")
    for item in report.get("sources", []):
        name = item.get("name", "")
        status = "ok" if item.get("ok") else "missing"
        lines.append(f"- {name}: {status}")
        missing = item.get("missing", {})
        if missing.get("bins"):
            lines.append(f"  bins: {', '.join(missing['bins'])}")
        if missing.get("any_bins"):
            formatted = ["/".join(group) for group in missing["any_bins"]]
            lines.append(f"  any_bins: {', '.join(formatted)}")
        if missing.get("env"):
            lines.append(f"  env: {', '.join(missing['env'])}")
        if missing.get("config"):
            lines.append(f"  config: {', '.join(missing['config'])}")
    lines.append("")

    summary = report.get("missing", {})
    lines.append("Summary:")
    lines.append(f"- bins: {', '.join(summary.get('bins', [])) or 'none'}")
    any_bins = summary.get("any_bins", [])
    lines.append(f"- any_bins: {', '.join('/'.join(group) for group in any_bins) or 'none'}")
    lines.append(f"- env: {', '.join(summary.get('env', [])) or 'none'}")
    lines.append(f"- config: {', '.join(summary.get('config', [])) or 'none'}")
    lines.append("")

    plan = report.get("plan", [])
    if not plan:
        lines.append("Install plan: none")
    else:
        lines.append("Install plan:")
        for step in plan:
            lines.append(f"- [{step.get('skill', '')}] {step.get('command', '')}")
    apply_result = report.get("apply_result", {}) if isinstance(report.get("apply_result", {}), dict) else {}
    steps = apply_result.get("steps", [])
    if steps:
        lines.append("")
        lines.append("Apply result:")
        for step in steps:
            status = step.get("status", "unknown")
            skill = step.get("skill", "")
            command = step.get("command", "")
            exit_code = step.get("exit_code")
            if exit_code is None:
                lines.append(f"- [{status}] {skill}: {command}")
            else:
                lines.append(f"- [{status}] {skill}: {command} (exit={exit_code})")
            stderr = step.get("stderr", "")
            if stderr:
                lines.append(f"  stderr: {stderr}")
    elif report.get("apply_requested"):
        lines.append("")
        lines.append("Apply result: no executable install steps")
    return "\n".join(lines)


def report_to_json(report: dict[str, Any]) -> str:
    """Return pretty JSON output for dependency report."""
    return json.dumps(report, ensure_ascii=False, indent=2)
