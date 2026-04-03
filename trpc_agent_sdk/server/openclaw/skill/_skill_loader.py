# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""OpenClaw skill repository aligned to trpc-agent-go behavior."""

import os
import re
import shutil
from pathlib import Path
from typing import Any
from typing import Optional
from typing_extensions import override
from urllib.parse import urlparse

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.skills import FsSkillRepository
from trpc_agent_sdk.skills import SkillSummary

from ..config import ClawConfig
from ..config import SkillConfig
from ..config import SkillRootConfig
from ._skill_parser import ClawSkillParser
from ._utils import download_file
from ._utils import extract_archive
from ._utils import normalize_allowlist
from ._utils import normalize_bundled_root
from ._utils import normalize_config_keys
from ._utils import normalize_skill_configs
from ._utils import prepare_dir
from ._utils import skill_file_in_dir


class ClawSkillLoader(FsSkillRepository):
    """OpenClaw-aware skill repository based on FsSkillRepository."""

    def __init__(
        self,
        config: ClawConfig,
        workspace_runtime: Optional[BaseWorkspaceRuntime] = None,
    ) -> None:
        self.skills_cfg: SkillRootConfig = config.skills

        self._workspace_root = Path(config.agent.workspace).expanduser().resolve()
        self._workspace_skills_root = self._workspace_root / "skills"
        self._workspace_skills_root.mkdir(parents=True, exist_ok=True)
        self._downloaded_skills_root = self._workspace_skills_root / "downloaded"
        self._downloaded_skills_root.mkdir(parents=True, exist_ok=True)

        self._normalize_skills_config(self.skills_cfg)
        self._bundled_root = self.skills_cfg.bundled_root
        self._skill_configs = self.skills_cfg.skill_configs or {}

        self.local_roots: list[str] = []
        self.local_file_roots: list[str] = []
        self.network_roots: list[str] = []
        self.builtin_roots: list[str] = []

        self._skill_source_by_name: dict[str, str] = {}
        self._skill_original_dir_by_name: dict[str, Path] = {}

        self._eligible: set[str] = set()
        self._reasons: dict[str, str] = {}
        self._skill_meta: dict[str, dict[str, Any]] = {}
        self._skill_has_openclaw_meta: dict[str, bool] = {}
        self._skill_key_map: dict[str, str] = {}
        self._skill_env_vars: dict[str, dict[str, str]] = {}

        self._claw_parser = ClawSkillParser(self.skills_cfg)

        roots = list(self.skills_cfg.skill_roots) + list(self.skills_cfg.builtin_skill_roots)
        super().__init__(*roots, workspace_runtime=workspace_runtime)

    @property
    def workspace_skills_root(self) -> Path:
        return self._workspace_skills_root

    @property
    def downloaded_skills_root(self) -> Path:
        return self._downloaded_skills_root

    @property
    def eligible_set(self) -> set[str]:
        return self._eligible

    @property
    def reasons(self) -> dict[str, str]:
        return self._reasons

    @property
    def skill_meta(self) -> dict[str, dict[str, Any]]:
        return self._skill_meta

    @property
    def skill_key_map(self) -> dict[str, str]:
        return self._skill_key_map

    def set_workspace_runtime(self, workspace_runtime: BaseWorkspaceRuntime) -> None:
        self._workspace_runtime = workspace_runtime

    @staticmethod
    def _normalize_skills_config(cfg: SkillRootConfig) -> None:
        cfg.config_keys = sorted(list(normalize_config_keys(cfg.config_keys)))
        cfg.allow_bundled = sorted(list(normalize_allowlist(cfg.allow_bundled)))
        cfg.skill_configs = normalize_skill_configs(cfg.skill_configs)
        cfg.bundled_root = normalize_bundled_root(cfg.bundled_root)
        cfg.skill_roots = [str(item).strip() for item in cfg.skill_roots if str(item).strip()]
        cfg.builtin_skill_roots = [str(item).strip() for item in cfg.builtin_skill_roots if str(item).strip()]

    @override
    def _resolve_skill_roots(self, roots: list[str]) -> None:
        local_base = self._workspace_skills_root / "local"
        local_file_base = self._workspace_skills_root / "local_file"
        network_base = self._workspace_skills_root / "network"
        builtin_base = self._workspace_skills_root / "builtin"
        for base in (local_base, local_file_base, network_base, builtin_base):
            prepare_dir(base)
        self._downloaded_skills_root.mkdir(parents=True, exist_ok=True)

        selected_roots: list[str] = []
        selected_names: set[str] = set()
        self.local_roots = []
        self.local_file_roots = []
        self.network_roots = []
        self.builtin_roots = []
        self._skill_source_by_name = {}
        self._skill_original_dir_by_name = {}

        for skill_dir in self._discover_skill_dirs(self._downloaded_skills_root):
            self._register_skill_root(
                skill_dir=skill_dir,
                source="workspace",
                roots=selected_roots,
                seen=selected_names,
                record=self.local_roots,
            )

        builtin_paths = [str(Path(p).expanduser().resolve()) for p in self.skills_cfg.builtin_skill_roots]
        builtin_path_set = set(builtin_paths)
        ordered_inputs: list[tuple[str, str]] = []
        builtin_inputs: list[str] = []
        seen_builtin_inputs: set[str] = set()

        for root in roots:
            raw = str(root).strip()
            if not raw:
                continue
            parsed = urlparse(raw)
            if not parsed.scheme:
                resolved = str(Path(raw).expanduser().resolve())
                if resolved in builtin_path_set:
                    if resolved not in seen_builtin_inputs:
                        builtin_inputs.append(resolved)
                        seen_builtin_inputs.add(resolved)
                else:
                    ordered_inputs.append(("local", raw))
                continue
            if parsed.scheme in {"http", "https"}:
                ordered_inputs.append(("network", raw))
                continue
            if parsed.scheme == "file":
                resolved = str(Path(parsed.path).expanduser().resolve())
                if resolved in builtin_path_set:
                    if resolved not in seen_builtin_inputs:
                        builtin_inputs.append(resolved)
                        seen_builtin_inputs.add(resolved)
                else:
                    ordered_inputs.append(("local_file", raw))
                continue
            logger.warning("Invalid skill root %s", raw)

        for path in builtin_paths:
            if path not in seen_builtin_inputs:
                builtin_inputs.append(path)

        for kind, raw in ordered_inputs:
            if kind == "local":
                for skill_dir in self._discover_skill_dirs(Path(raw).expanduser().resolve()):
                    self._register_skill_root(
                        skill_dir=skill_dir,
                        source="workspace",
                        roots=selected_roots,
                        seen=selected_names,
                        record=self.local_roots,
                        link_base=local_base,
                    )
                continue

            if kind == "local_file":
                parsed = urlparse(raw)
                local_path = Path(parsed.path).expanduser().resolve()
                if local_path.is_dir():
                    for skill_dir in self._discover_skill_dirs(local_path):
                        self._register_skill_root(
                            skill_dir=skill_dir,
                            source="workspace",
                            roots=selected_roots,
                            seen=selected_names,
                            record=self.local_file_roots,
                            link_base=local_file_base,
                        )
                elif local_path.is_file():
                    extract_dir = local_file_base / (local_path.stem or "skills")
                    prepare_dir(extract_dir)
                    try:
                        extract_archive(local_path, extract_dir)
                    except Exception as exc:  # pylint: disable=broad-except
                        logger.warning("Failed to extract local skill archive %s: %s", local_path, exc)
                        continue
                    for skill_dir in self._discover_skill_dirs(extract_dir):
                        self._register_skill_root(
                            skill_dir=skill_dir,
                            source="workspace",
                            roots=selected_roots,
                            seen=selected_names,
                            record=self.local_file_roots,
                        )
                else:
                    logger.warning("Local file skill root not found: %s", raw)
                continue

            if kind == "network":
                parsed = urlparse(raw)
                archive_name = Path(parsed.path).name or "skills.zip"
                stem = Path(archive_name).stem or "network-skill"
                download_path = network_base / archive_name
                extract_dir = network_base / stem
                prepare_dir(extract_dir)
                try:
                    download_file(raw, download_path)
                    extract_archive(download_path, extract_dir)
                except Exception as exc:  # pylint: disable=broad-except
                    logger.warning("Failed to download/extract network skill root %s: %s", raw, exc)
                    download_path.unlink(missing_ok=True)
                    continue
                download_path.unlink(missing_ok=True)
                for skill_dir in self._discover_skill_dirs(extract_dir):
                    self._register_skill_root(
                        skill_dir=skill_dir,
                        source="network",
                        roots=selected_roots,
                        seen=selected_names,
                        record=self.network_roots,
                    )

        for raw in builtin_inputs:
            for skill_dir in self._discover_skill_dirs(Path(raw).expanduser().resolve()):
                self._register_skill_root(
                    skill_dir=skill_dir,
                    source="builtin",
                    roots=selected_roots,
                    seen=selected_names,
                    record=self.builtin_roots,
                    link_base=builtin_base,
                )

        self._skill_roots = selected_roots

    def _discover_skill_dirs(self, root: Path) -> list[Path]:
        if not root.exists():
            return []
        discovered: set[Path] = set()
        if root.is_dir():
            if skill_file_in_dir(root) is not None:
                discovered.add(root.resolve())
            for path in root.rglob("SKILL.md"):
                discovered.add(path.parent.resolve())
            for path in root.rglob("skill.md"):
                discovered.add(path.parent.resolve())
        return sorted(discovered)

    def _safe_link_name(self, src: Path, base: Path) -> str:
        name = re.sub(r"[^A-Za-z0-9_.-]+", "-", (src.name.strip() or "skill")).strip("-._")
        if not name:
            name = "skill"
        out = name
        idx = 1
        while (base / out).exists():
            out = f"{name}-{idx}"
            idx += 1
        return out

    def _link_to_base(self, src: Path, base: Path) -> Path:
        base.mkdir(parents=True, exist_ok=True)
        link_path = base / self._safe_link_name(src, base)
        if link_path.exists() or link_path.is_symlink():
            if link_path.is_dir() and not link_path.is_symlink():
                shutil.rmtree(link_path, ignore_errors=True)
            else:
                link_path.unlink(missing_ok=True)
        os.symlink(src, link_path, target_is_directory=True)
        return link_path

    def _read_skill_name(self, skill_file: Path) -> str:
        return self._claw_parser.read_skill_name(skill_file, self.from_markdown)

    def _register_skill_root(
        self,
        *,
        skill_dir: Path,
        source: str,
        roots: list[str],
        seen: set[str],
        record: list[str],
        link_base: Optional[Path] = None,
    ) -> None:
        skill_file = skill_file_in_dir(skill_dir)
        if skill_file is None:
            return
        skill_name = self._read_skill_name(skill_file) or skill_dir.name
        if not skill_name:
            return
        if skill_name in seen:
            logger.warning("Duplicate skill '%s' from %s ignored by priority rules.", skill_name, skill_dir)
            return
        managed_dir = self._link_to_base(skill_dir, link_base) if link_base is not None else skill_dir
        seen.add(skill_name)
        roots.append(str(managed_dir))
        record.append(str(managed_dir))
        self._skill_source_by_name[skill_name] = source
        self._skill_original_dir_by_name[skill_name] = skill_dir.resolve()

    @override
    def _index(self) -> None:
        super()._index()
        eligible: set[str] = set()
        reasons: dict[str, str] = {}
        skill_meta: dict[str, dict[str, Any]] = {}
        has_openclaw_meta: dict[str, bool] = {}
        skill_key_map: dict[str, str] = {}
        skill_env_vars: dict[str, dict[str, str]] = {}

        for name in sorted(self._skill_paths):
            meta, has_meta = self._read_openclaw_meta(name)
            skill_meta[name] = meta
            has_openclaw_meta[name] = has_meta
            skill_key = str(meta.get("skill_key", "")).strip() or name
            skill_key_map[name] = skill_key

            reason = self._evaluate_skill(name=name, meta=meta)
            if reason:
                reasons[name] = reason
                if self.skills_cfg.debug and reason:
                    logger.info("skip skill %s: %s", name, reason)
                continue

            eligible.add(name)
            skill_env_vars[name] = self._build_skill_run_env(name, skill_key, meta)

        self._eligible = eligible
        self._reasons = reasons
        self._skill_meta = skill_meta
        self._skill_has_openclaw_meta = has_openclaw_meta
        self._skill_key_map = skill_key_map
        self._skill_env_vars = skill_env_vars

    def _read_openclaw_meta(self, name: str) -> tuple[dict[str, Any], bool]:
        md = self._get_skill_metadata(name) or {}
        raw_meta = md.get("metadata", {})
        parsed = self._claw_parser.parse_metadata(raw_meta)
        has_openclaw = bool(parsed)
        return self._normalize_skill_meta(parsed), has_openclaw

    @staticmethod
    def _normalize_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _normalize_skill_meta(self, raw: dict[str, Any]) -> dict[str, Any]:
        data = dict(raw or {})
        req = data.get("requires", {}) if isinstance(data.get("requires"), dict) else {}
        return {
            "skill_key": str(data.get("skill_key", "")).strip(),
            "always": bool(data.get("always", False)),
            "os": self._normalize_list(data.get("os", [])),
            "requires": {
                "bins": self._normalize_list(req.get("bins", [])),
                "any_bins": self._normalize_list(req.get("any_bins", [])),
                "env": self._normalize_list(req.get("env", [])),
                "config": self._normalize_list(req.get("config", [])),
            },
            "install": str(data.get("install", "")).strip(),
        }

    def _evaluate_skill(self, *, name: str, meta: dict[str, Any]) -> str:
        source = "builtin" if self._is_bundled_skill(self._skill_paths.get(name, "")) else "workspace"
        return self._claw_parser.evaluate_skill_eligibility(
            skill_name=name,
            source=source,
            skill_meta=meta,
        ) or ""

    def _is_bundled_skill(self, base_dir: str) -> bool:
        root = str(self._bundled_root or "").strip()
        if not root or not str(base_dir).strip():
            return False
        try:
            base = Path(base_dir).expanduser().resolve()
            bundled = Path(root).expanduser().resolve()
        except Exception:  # pylint: disable=broad-except
            return False
        if base == bundled:
            return False
        try:
            base.relative_to(bundled)
            return True
        except Exception:  # pylint: disable=broad-except
            return False

    @override
    def summaries(self) -> list[SkillSummary]:
        return [summary for summary in super().summaries() if summary.name in self._eligible]

    @override
    def get(self, name: str):
        key = str(name).strip()
        if not key:
            raise ValueError("empty skill name")
        if key not in self._eligible:
            reason = self._reasons.get(key, "")
            if reason:
                raise ValueError(f"skill '{key}' is disabled: {reason}")
            raise ValueError(f"skill '{key}' is disabled")

        skill = super().get(key)
        base_dir = self._skill_paths.get(key, "")
        if base_dir:
            skill.body = skill.body.replace("{BASE_DIR}", base_dir).replace("{{BASE_DIR}}", base_dir)
            for res in skill.resources:
                if res.content:
                    res.content = res.content.replace("{BASE_DIR}", base_dir).replace("{{BASE_DIR}}", base_dir)
        return skill

    @override
    def path(self, name: str) -> str:
        key = str(name).strip()
        if not key:
            raise ValueError("empty skill name")
        if key not in self._eligible:
            reason = self._reasons.get(key, "")
            if reason:
                raise ValueError(f"skill '{key}' is disabled: {reason}")
            raise ValueError(f"skill '{key}' is disabled")
        return super().path(key)

    @override
    def refresh(self) -> None:
        super().refresh()

    def _resolve_skill_config(self, skill_key: str, skill_name: str) -> Optional[SkillConfig]:
        key = str(skill_key).strip()
        if key and key in self._skill_configs:
            return self._skill_configs[key]
        name = str(skill_name).strip()
        if name and name in self._skill_configs:
            return self._skill_configs[name]
        return None

    def _build_skill_run_env(self, skill_name: str, skill_key: str, meta: dict[str, Any]) -> dict[str, str]:
        cfg = self._resolve_skill_config(skill_key, skill_name)
        out: dict[str, str] = {}

        # Priority 1: explicit per-skill config env
        if cfg is not None:
            for key, val in (cfg.env or {}).items():
                k = str(key).strip()
                v = str(val).strip()
                if not k or not v or self._claw_parser.is_blocked_skill_env_key(k):
                    continue
                out[k] = v

        # Priority 2: fallback to host env for keys declared in skill metadata requires.env
        requires = meta.get("requires", {}) if isinstance(meta.get("requires"), dict) else {}
        required_env_keys = requires.get("env", []) if isinstance(requires.get("env"), list) else []
        for raw_key in required_env_keys:
            key = str(raw_key).strip()
            if not key or key in out or self._claw_parser.is_blocked_skill_env_key(key):
                continue
            value = str(os.environ.get(key, "")).strip()
            if value:
                out[key] = value
        return out

    @override
    def skill_run_env(self, skill_name: str) -> dict[str, str]:
        key = str(skill_name).strip()
        if not key:
            return {}
        return dict(self._skill_env_vars.get(key, {}))

    def _iter_all_indexed(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for name in sorted(self._skill_paths):
            base = self._skill_paths[name]
            items.append({
                "name": name,
                "base_dir": base,
                "path": str(Path(base) / "SKILL.md"),
                "enabled": name in self._eligible,
                "reason": self._reasons.get(name, ""),
                "source": self._skill_source_by_name.get(name, "workspace"),
                "meta": self._skill_meta.get(name, {}),
                "description": self._all_descriptions.get(name, "").strip(),
            })
        return items

    @override
    def skill_list(self, mode: str = "all") -> dict:
        normalized_mode = str(mode or "all").strip().lower()
        if normalized_mode not in {"all", "enabled", "disabled"}:
            normalized_mode = "all"

        all_items = self._iter_all_indexed()
        entries: list[dict[str, Any]] = []
        for item in all_items:
            if normalized_mode == "enabled" and not item["enabled"]:
                continue
            if normalized_mode == "disabled" and item["enabled"]:
                continue
            meta = item["meta"] if isinstance(item["meta"], dict) else {}
            entries.append({
                "name": item["name"],
                "description": item["description"],
                "enabled": item["enabled"],
                "reason": item["reason"],
                "source": item["source"],
                "path": item["path"],
                "skill_key": str(meta.get("skill_key", "")).strip(),
                "always": bool(meta.get("always", False)),
                "requires": meta.get("requires", {}),
                "install": str(meta.get("install", "")).strip(),
            })

        return {
            "mode": normalized_mode,
            "total": len(all_items),
            "enabled_count": len(self._eligible),
            "disabled_count": len(self._reasons),
            "entries": entries,
        }

    def dependency_sources(self, names: Optional[list[str]] = None) -> list[dict]:
        selected: list[str] = []
        seen: set[str] = set()
        for raw in names or []:
            for part in str(raw).split(","):
                item = part.strip()
                if not item or item in seen:
                    continue
                seen.add(item)
                selected.append(item)

        want_all = len(selected) == 0
        out: list[dict[str, Any]] = []
        for name, meta in sorted(self._skill_meta.items()):
            if not self._skill_has_openclaw_meta.get(name, False):
                continue
            if not want_all and name not in selected:
                continue
            out.append({
                "name": name,
                "description": self._all_descriptions.get(name, "").strip(),
                "requires": meta.get("requires", {}) if isinstance(meta, dict) else {},
                "install": str(meta.get("install", "")).strip() if isinstance(meta, dict) else "",
            })

        if not want_all:
            included = {str(item["name"]).strip() for item in out}
            missing = [name for name in selected if name not in included]
            if missing:
                raise ValueError(f"unknown skill: {', '.join(missing)}")
        return out

    def set_skill_enabled(self, config_key: str, enabled: bool) -> None:
        """Go parity: update skill_configs[config_key].enabled and re-index."""
        key = str(config_key).strip()
        if not key:
            raise ValueError("skill config key is required")
        cfg = self._skill_configs.get(key, SkillConfig())
        cfg.enabled = bool(enabled)
        self._skill_configs[key] = cfg
        self.skills_cfg.skill_configs = self._skill_configs
        self.refresh()

    def user_prompt(self) -> str:
        if not self._reasons:
            return ""
        lines = [
            "# Skills",
            "",
            "Some skills are currently disabled in this environment:",
        ]
        for name in sorted(self._reasons):
            lines.append(f"- {name}: {self._reasons[name]}")
        return "\n".join(lines) + "\n"

    def _get_skill_metadata(self, name: str) -> Optional[dict[str, Any]]:
        try:
            skill_path = Path(self._skill_paths[name]) / "SKILL.md"
            content = skill_path.read_text(encoding="utf-8")
            front_matter, _ = self.from_markdown(content)
            return front_matter
        except Exception:  # pylint: disable=broad-except
            return None
