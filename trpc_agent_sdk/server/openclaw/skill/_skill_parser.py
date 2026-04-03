# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Skill parsing and validation helpers for ClawSkillLoader."""

from __future__ import annotations

import ast
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Optional

from ..config import SkillConfig
from ..config import SkillRootConfig

_ENV_LD_PRELOAD = "LD_PRELOAD"
_ENV_LD_LIBRARY_PATH = "LD_LIBRARY_PATH"
_ENV_DYLD_INSERT_LIBRARIES = "DYLD_INSERT_LIBRARIES"
_ENV_DYLD_LIBRARY_PATH = "DYLD_LIBRARY_PATH"
_ENV_DYLD_FORCE_FLAT_NAMESPACE = "DYLD_FORCE_FLAT_NAMESPACE"
_ENV_OPENSSL_CONF = "OPENSSL_CONF"


class ClawSkillParser:
    """Encapsulate skill metadata parsing and capability checks."""

    def __init__(self, skill_root_config: SkillRootConfig) -> None:
        """Initialize the skill parser."""
        self._config_keys: set[str] = self._normalize_config_keys(skill_root_config.config_keys)
        self._allow_bundled: set[str] = self._normalize_allowlist(skill_root_config.allow_bundled)
        self._skill_configs: dict[str, SkillConfig] = self._normalize_skill_configs(skill_root_config.skill_configs)

    def read_skill_name(self, skill_file: Path, from_markdown: Callable[[str], tuple[dict, str]]) -> str:
        """Read skill name from frontmatter."""
        try:
            content = skill_file.read_text(encoding="utf-8")
            metadata, _ = from_markdown(content)
            skill_name = metadata.get("name", "")
            return str(skill_name).strip() if skill_name else ""
        except Exception:  # pylint: disable=broad-except
            return ""

    def parse_metadata(self, raw: dict[str, Any]) -> dict:
        """Parse the metadata field into a nanobot/openclaw dict."""
        if not raw:
            return {}
        data = raw
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return {}
            # Preferred path: strict JSON.
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, TypeError):
                # Compatibility path: Python-dict-style literals from YAML -> str(dict).
                try:
                    data = ast.literal_eval(text)
                except (ValueError, SyntaxError):
                    return {}
        # SKILL.md commonly nests OpenClaw metadata under metadata.openclaw
        # Normalize to the inner object for downstream consumers.
        if isinstance(data, dict) and isinstance(data.get("openclaw"), dict):
            return data["openclaw"]
        return data

    def _resolve_skill_config(self, skill_key: str, skill_name: str) -> SkillConfig:
        """Resolve per-skill runtime config by skill_key then name."""
        key = skill_key.strip()
        if key in self._skill_configs:
            return self._skill_configs[key]
        name = skill_name.strip()
        if name in self._skill_configs:
            return self._skill_configs[name]
        return SkillConfig()

    def evaluate_skill_eligibility(
        self,
        *,
        skill_name: str = "",
        source: str,
        skill_meta: dict,
    ) -> Optional[str]:
        """Return whether a skill is eligible and the disable reason."""
        resolved_name = str(skill_name).strip()
        resolved_key = str(skill_meta.get("skill_key", "")).strip()
        cfg = self._resolve_skill_config(resolved_key, resolved_name)
        # Default behavior: enabled unless explicitly disabled.
        if cfg.enabled is False:
            return "disabled by config"

        if source == "builtin" and self._allow_bundled:
            if resolved_key not in self._allow_bundled and resolved_name not in self._allow_bundled:
                return "blocked by allow_bundled"

        if skill_meta.get("always"):
            return ""

        requires: dict[str, Any] = skill_meta.get("requires", {})
        reason = self._evaluate_openclaw_os(skill_meta.get("os", []))
        if reason:
            return reason
        reason = self._evaluate_required_bins(requires.get("bins", []))
        if reason:
            return reason
        reason = self._evaluate_required_any_bins(requires.get("any_bins", []))
        if reason:
            return reason
        reason = self._evaluate_required_env(requires.get("env", []), skill_meta, cfg)
        if reason:
            return reason
        reason = self._evaluate_required_config(requires.get("config", []))
        if reason:
            return reason
        return ""

    def has_config_key(self, want: str) -> bool:
        """Check whether config key is available."""
        if not self._config_keys:
            return False
        if want in self._config_keys:
            return True
        prefix = f"{want}."
        return any(key.startswith(prefix) for key in self._config_keys)

    def _evaluate_openclaw_os(self, allowlist: object) -> Optional[str]:
        raw_allow = allowlist if isinstance(allowlist, list) else []
        allow = [str(item).strip().lower() for item in raw_allow if str(item).strip()]
        if not allow:
            return ""
        host_os = sys.platform.lower()
        if host_os.startswith("linux"):
            host_os = "linux"
        elif host_os.startswith("darwin"):
            host_os = "darwin"
        elif host_os.startswith("win"):
            host_os = "windows"
        allow = [{"win32": "windows"}.get(item, item) for item in allow]
        if host_os in allow:
            return ""
        return f"os mismatch (allowed: {', '.join(allow)})"

    @staticmethod
    def _evaluate_required_bins(bins: object) -> str:
        values = bins if isinstance(bins, list) else []
        missing = [
            str(bin_name).strip() for bin_name in values
            if str(bin_name).strip() and not shutil.which(str(bin_name).strip())
        ]
        if not missing:
            return ""
        return f"missing bins: {', '.join(missing)}"

    @staticmethod
    def _evaluate_required_any_bins(bins: object) -> str:
        values = [
            str(bin_name).strip() for bin_name in (bins if isinstance(bins, list) else []) if str(bin_name).strip()
        ]
        if not values:
            return ""
        for bin_name in values:
            if shutil.which(bin_name):
                return ""
        return f"missing any_bins (need one): {', '.join(values)}"

    def _evaluate_required_env(self, env_names: object, skill_meta: dict, cfg: SkillConfig) -> str:
        values = env_names if isinstance(env_names, list) else []
        cfg_env = cfg.env
        missing: list[str] = []
        for name in values:
            key = str(name).strip()
            if not key:
                continue
            if os.environ.get(key, "").strip():
                continue
            if self.is_blocked_skill_env_key(key):
                missing.append(key)
                continue
            if str(cfg_env.get(key, "")).strip():
                continue
            missing.append(key)
        if not missing:
            return ""
        return f"missing env: {', '.join(missing)}"

    def _evaluate_required_config(self, requires_config_keys: list[str]) -> str:
        values = [str(key).strip().lower() for key in requires_config_keys if str(key).strip()]
        if not values:
            return ""
        missing = [key for key in values if not self.has_config_key(key)]
        if not missing:
            return ""
        return f"missing config: {', '.join(missing)}"

    @staticmethod
    def _normalize_config_keys(keys: list[str]) -> set[str]:
        values = keys if isinstance(keys, list) else []
        return {key.strip().lower() for key in values if key.strip()}

    @staticmethod
    def _normalize_allowlist(keys: list[str]) -> set[str]:
        values = keys if isinstance(keys, list) else []
        return {key.strip() for key in values if key.strip()}

    @staticmethod
    def _normalize_skill_configs(skill_configs: dict[str, SkillConfig]) -> dict[str, SkillConfig]:
        out: dict[str, SkillConfig] = {}
        for key, cfg in skill_configs.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            if isinstance(cfg, dict):
                cfg = SkillConfig.model_validate(cfg)
            cfg.env = {
                str(k).strip(): str(v).strip()
                for k, v in (cfg.env or {}).items() if str(k).strip() and str(v).strip()
            }
            out[normalized_key] = cfg
        return out

    @staticmethod
    def is_blocked_skill_env_key(key: str) -> bool:
        """Whether env key should be blocked for safety."""
        name = key.strip().upper()
        return name in {
            _ENV_LD_PRELOAD,
            _ENV_LD_LIBRARY_PATH,
            _ENV_DYLD_INSERT_LIBRARIES,
            _ENV_DYLD_LIBRARY_PATH,
            _ENV_DYLD_FORCE_FLAT_NAMESPACE,
            _ENV_OPENSSL_CONF,
        }
