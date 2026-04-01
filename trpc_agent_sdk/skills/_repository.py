# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Skill repository module.

This module provides a model-agnostic Agent Skills repository.
A skill is a folder containing a SKILL.md file with YAML front
matter and a Markdown body, plus optional doc files.
"""

from __future__ import annotations

import abc
import os
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from typing_extensions import override

import yaml

from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.code_executors import create_local_workspace_runtime
from trpc_agent_sdk.log import logger

from ._constants import SKILL_FILE
from ._types import Skill
from ._types import SkillResource
from ._types import SkillSummary
from ._url_root import SkillRootResolver


class BaseSkillRepository(abc.ABC):
    """
    Base class for a source of skills.

    This is an abstract interface that defines the contract
    for skill repositories.
    """

    def __init__(self, workspace_runtime: BaseWorkspaceRuntime):
        self._workspace_runtime = workspace_runtime

    @property
    def workspace_runtime(self) -> BaseWorkspaceRuntime:
        return self._workspace_runtime

    def _parse_summary(self, header: dict, out: Skill) -> None:
        """
        Return the names of all available skills.
        """
        out.summary.name = header.get('name', '')
        out.summary.description = header.get('description', '')

    def _parse_body(self, body: str, out: Skill) -> None:
        """
        Return the body of the skill.
        """
        out.body = body

    @abc.abstractmethod
    def _parse_all(self, path: str, out: Skill) -> None:
        """
        Return the body of the skill.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def summaries(self) -> List[SkillSummary]:
        """
        Return all available skill summaries.

        Returns:
            List of skill summaries
        """
        raise NotImplementedError

    def user_prompt(self) -> str:
        """
        Return the user prompt for the skill repository.
        """
        return ""

    @abc.abstractmethod
    def get(self, name: str) -> Skill:
        """
        Return a full skill by name.

        Args:
            name: The skill name

        Returns:
            The full skill object

        Raises:
            Exception: If skill not found
        """
        raise NotImplementedError

    @abc.abstractmethod
    def skill_list(self) -> list[str]:
        """
        Return the names of all available skills.

        Returns:
            List of skill names
        """
        raise NotImplementedError

    @abc.abstractmethod
    def path(self, name: str) -> str:
        """
        Return the directory path that contains the given skill.

        This allows staging the whole skill folder for execution.

        Args:
            name: The skill name

        Returns:
            The directory path containing the skill

        Raises:
            Exception: If skill not found
        """
        raise NotImplementedError


class FsSkillRepository(BaseSkillRepository):
    """
    Implements BaseSkillRepository backed by filesystem roots.

    This class scans one or more root directories for skills
    and provides access to them.

    Attributes:
        roots: List of root directories to scan
        index: Mapping from skill name to directory path
    """

    def __init__(self, *roots: str, workspace_runtime: Optional[BaseWorkspaceRuntime] = None):
        """
        Create a FsSkillRepository scanning the given roots.

        Args:
            *roots: Variable number of root directory paths

        Raises:
            Exception: If scanning fails
        """
        if workspace_runtime is None:
            workspace_runtime = create_local_workspace_runtime()
        super().__init__(workspace_runtime)
        self._skill_paths: dict[str, str] = {}
        self._skill_roots: list[str] = []
        if isinstance(roots, tuple):
            new_roots = []
            for root in roots:
                if isinstance(root, str):
                    new_roots.append(root)
                elif isinstance(root, list):
                    new_roots.extend(root)
            roots = new_roots
        self._resolve_skill_roots(roots)
        self._scan()

    def _resolve_skill_roots(self, roots: list[str]) -> None:
        """
        Resolve a skill root string to a local directory path.

        Args:
            roots: The list of skill root strings to resolve.

        Returns:
            The local directory path.
        """
        for root in roots:
            try:
                path = SkillRootResolver(root).resolve()
                self._skill_roots.append(path)
            except Exception as ex:
                logger.warning("Failed to resolve skill root %s: %s", root, ex)
                continue

    @override
    def path(self, name: str) -> str:
        """
        Return the directory path that contains the given skill.

        This allows staging the whole skill folder for execution.

        Args:
            name: The skill name

        Returns:
            The directory path containing the skill

        Raises:
            Exception: If skill not found
        """
        if name not in self._skill_paths:
            raise ValueError(f"skill '{name}' not found")
        return self._skill_paths[name]

    def _scan(self) -> None:
        """
        Scan all root directories for skills.

        This method walks through each root directory, looking for
        SKILL.md files and indexing them by skill name.

        Raises:
            Exception: If scanning encounters errors
        """
        seen = set()

        for root in self._skill_roots:
            if not root:
                continue

            # Clean and resolve the path
            root_path = Path(root).resolve()

            # Skip if already seen
            if str(root_path) in seen:
                continue
            seen.add(str(root_path))

            # Walk the directory tree
            try:
                for dirpath, dirnames, filenames in os.walk(root_path):
                    # Check if SKILL.md exists in this directory
                    skill_file_path = Path(dirpath) / SKILL_FILE

                    if not skill_file_path.is_file():
                        continue

                    # Try to parse the summary
                    try:
                        skill = Skill()
                        front_matter, _ = self._parse_yaml(str(skill_file_path))
                        self._parse_summary(front_matter, skill)
                        if not skill.summary.name:
                            continue

                        # Record first occurrence; later ones ignored
                        if skill.summary.name not in self._skill_paths:
                            self._skill_paths[skill.summary.name] = dirpath
                            logger.debug("Found skill '%s' at %s in %s files %s", skill.summary.name, dirpath, dirnames,
                                         filenames)
                    except Exception as ex:  # pylint: disable=broad-except
                        logger.debug("Failed to parse skill at %s: %s", skill_file_path, ex)
                        continue

            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Error scanning root %s: %s", root_path, ex)
                continue

    def _parse_yaml(self, path: str) -> tuple[dict, str]:
        """
        Parse front matter name/description only from a skill file.

        Args:
            path: Path to the skill file

        Returns:
            Tuple of (front_matter dict, body string)

        Raises:
            Exception: If file cannot be read or parsed
        """
        skill_path = Path(path)
        try:
            content = skill_path.read_text(encoding='utf-8')
        except Exception as ex:  # pylint: disable=broad-except
            raise Exception(f"Failed to read file {skill_path}: {ex}")

        return self.from_markdown(content)

    @override
    def summaries(self) -> List[SkillSummary]:
        """
        Return all available skill summaries.

        Returns:
            List of skill summaries
        """
        out = []

        for name, dir_path in self._skill_paths.items():
            skill_file_path = Path(dir_path) / SKILL_FILE

            try:
                skill = Skill()
                front_matter, _ = self._parse_yaml(str(skill_file_path))
                self._parse_summary(front_matter, skill)
                summary = skill.summary
                if not summary.name:
                    summary.name = name
                out.append(summary)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to parse summary for skill '%s': %s", name, ex)
                continue

        return out

    @override
    def get(self, name: str) -> Skill:
        """
        Return a full skill by name.

        Args:
            name: The skill name

        Returns:
            The full skill object

        Raises:
            Exception: If skill not found or parsing fails
        """
        if name not in self._skill_paths:
            raise ValueError(f"skill '{name}' not found")

        dir_path = Path(self._skill_paths[name])
        skill_file_path = dir_path / SKILL_FILE

        # Parse the skill file
        skill = Skill()
        self._parse_all(str(skill_file_path), skill)

        if not skill.summary.name:
            skill.summary.name = name

        # Collect auxiliary documents (recursively)
        resources = skill.resources

        # Recursively find all files in the skill directory
        for entry in dir_path.rglob('*'):
            if not entry.is_file() or entry.name.startswith('.') or '.git' in entry.parts:
                continue

            entry_name = entry.name

            # Skip the main skill file
            if entry_name.lower() == SKILL_FILE.lower():
                continue

            # Only include doc files
            if not self._is_doc_file(entry_name):
                continue

            try:
                content = entry.read_text(encoding='utf-8')
                # Use relative path from skill directory to preserve subdirectory structure
                relative_path = entry.relative_to(dir_path)
                resources.append(SkillResource(path=str(relative_path), content=content))
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to read doc file %s: %s", entry, ex)
                continue

        return skill

    @override
    def skill_list(self) -> list[str]:
        """
        Return the names of all available skills.

        Returns:
            List of skill names
        """

        return list(self._skill_paths.keys())

    @override
    def _parse_body(self, body: str, out: Skill) -> None:
        """
        Return the body of the skill.
        """
        out.body = body
        out.tools = self._parse_tools_from_body(body)

    @override
    def _parse_all(self, path: str, out: Skill) -> None:
        """
        Parse front matter, Markdown body, and tool names from a skill file.

        Args:
            path: Path to the skill file

        Returns:
            Tuple of (Summary, body content, tool names)

        Raises:
            Exception: If file cannot be read or parsed
        """
        front_matter, body = self._parse_yaml(path)
        self._parse_summary(front_matter, out)
        self._parse_body(body, out)

    @classmethod
    def from_markdown(cls, content: str) -> tuple[Dict[str, str], str]:
        """
        Split content into front matter map and body.

        Front matter is expected to be in YAML format between --- delimiters.

        Args:
            text: The full text content

        Returns:
            Tuple of (front_matter dict, body string)
        """
        # Parse YAML frontmatter (between --- markers)
        if not content.startswith("---"):
            raise ValueError("SKILL.md must start with YAML frontmatter (---)")

        parts = content.split("---", 2)
        if len(parts) < 3:
            raise ValueError("SKILL.md must have YAML frontmatter between --- markers")

        yaml_content = parts[1].strip()
        markdown_content = parts[2].strip()

        try:
            metadata_dict = yaml.safe_load(yaml_content)
            if not isinstance(metadata_dict, dict):
                raise ValueError("YAML frontmatter must be a dictionary")
        except yaml.YAMLError as ex:
            raise ValueError(f"Invalid YAML in frontmatter: {ex}") from ex

        return metadata_dict, markdown_content

    @staticmethod
    def _is_doc_file(name: str) -> bool:
        """
        Check if a filename is a documentation file.

        Args:
            name: The filename to check

        Returns:
            True if the file is a doc file (.md or .txt)
        """
        name_lower = name.lower()
        return name_lower.endswith('.md') or name_lower.endswith('.txt')

    @staticmethod
    def _parse_tools_from_body(body: str) -> list[str]:
        """
        Parse tool names from the Tools section in SKILL.md body.

        The Tools section should be formatted as:
        ```
        Tools:
        - tool_name_1
        - tool_name_2
        # - commented_tool (this will be ignored)
        ```

        Args:
            body: The markdown body content from SKILL.md

        Returns:
            List of tool names found in the Tools section

        Rules:
        1. Tools section is case-insensitive (Tools:, tools:, TOOLS:)
        2. Tool names start with "-" on a new line
        3. Lines starting with "#" are treated as comments and ignored
        4. Tool names are stripped of whitespace
        """
        tool_names = []
        lines = body.split('\n')
        in_tools_section = False

        for line in lines:
            stripped_line = line.strip()

            # Check if we're entering the Tools section (case-insensitive)
            if stripped_line.lower().startswith('tools:'):
                in_tools_section = True
                continue

            # If we're in the Tools section
            if in_tools_section:
                # Stop parsing if we hit another section (starts with capital letter followed by colon)
                # or if we hit a blank line followed by a section header
                if stripped_line and not stripped_line.startswith('-') and not stripped_line.startswith('#'):
                    # Check if it's a section header (e.g., "Overview", "Examples")
                    if ':' in stripped_line or (stripped_line and stripped_line[0].isupper() and any(
                            stripped_line.startswith(s)
                            for s in ['Overview', 'Examples', 'Usage', 'Description', 'Installation'])):
                        break

                # Skip comments (lines starting with #)
                if stripped_line.startswith('#'):
                    continue

                # Parse tool names (lines starting with -)
                if stripped_line.startswith('-'):
                    # Remove the leading "-" and strip whitespace
                    tool_name = stripped_line[1:].strip()

                    # Skip if it's a comment after the dash (e.g., "- # commented")
                    if tool_name.startswith('#'):
                        continue

                    # Skip empty lines
                    if not tool_name:
                        continue

                    tool_names.append(tool_name)
                    logger.debug("Found tool in SKILL.md: %s", tool_name)

        return tool_names


def create_default_skill_repository(*roots: str,
                                    workspace_runtime: Optional[BaseWorkspaceRuntime] = None) -> FsSkillRepository:
    """
    Create a new filesystem skill repository.

    Args:
        roots: List of root directories to scan
        workspace_runtime: Optional workspace runtime to use

    Returns:
        A new default skill repository
    """
    if workspace_runtime is None:
        workspace_runtime = create_local_workspace_runtime()
    return FsSkillRepository(*roots, workspace_runtime=workspace_runtime)
