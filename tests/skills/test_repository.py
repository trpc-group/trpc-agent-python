# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import tempfile
from pathlib import Path
from unittest.mock import Mock
from unittest.mock import patch

import pytest
from trpc_agent_sdk.code_executors import BaseWorkspaceRuntime
from trpc_agent_sdk.skills import SKILL_FILE
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import FsSkillRepository
from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.skills import Skill
from trpc_agent_sdk.skills import SkillSummary


class ConcreteSkillRepository(BaseSkillRepository):
    """Concrete implementation of BaseSkillRepository for testing."""

    def summaries(self):
        return [SkillSummary(name="test-skill", description="Test")]

    def get(self, name: str) -> Skill:
        return Skill(summary=SkillSummary(name=name, description="Test"))

    def skill_list(self) -> list[str]:
        return ["test-skill"]

    def path(self, name: str) -> str:
        return f"/path/to/{name}"

    def _parse_all(self, path: str, out: Skill) -> None:
        out.summary.name = "test-skill"
        out.summary.description = "Test"
        out.body = "Body"


class TestBaseSkillRepository:
    """Test suite for BaseSkillRepository class."""

    def test_cannot_instantiate_abstract_class(self):
        """Test that BaseSkillRepository cannot be instantiated directly."""
        mock_runtime = Mock(spec=BaseWorkspaceRuntime)
        with pytest.raises(TypeError):
            BaseSkillRepository(mock_runtime)

    def test_concrete_repository_instantiation(self):
        """Test that concrete repository can be instantiated."""
        mock_runtime = Mock(spec=BaseWorkspaceRuntime)
        repo = ConcreteSkillRepository(mock_runtime)

        assert isinstance(repo, BaseSkillRepository)
        assert repo.workspace_runtime == mock_runtime

    def test_workspace_runtime_property(self):
        """Test workspace_runtime property."""
        mock_runtime = Mock(spec=BaseWorkspaceRuntime)
        repo = ConcreteSkillRepository(mock_runtime)

        assert repo.workspace_runtime == mock_runtime


class TestFsSkillRepository:
    """Test suite for FsSkillRepository class."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        self.mock_runtime = Mock(spec=BaseWorkspaceRuntime)

    def test_init_with_roots(self):
        """Test initialization with root directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)

            assert repo._skill_roots == [tmpdir]
            assert isinstance(repo._skill_paths, dict)

    def test_init_with_multiple_roots(self):
        """Test initialization with multiple root directories."""
        with tempfile.TemporaryDirectory() as tmpdir1:
            with tempfile.TemporaryDirectory() as tmpdir2:
                repo = FsSkillRepository(tmpdir1, tmpdir2, workspace_runtime=self.mock_runtime)

                assert len(repo._skill_roots) == 2

    def test_init_without_workspace_runtime(self):
        """Test initialization without workspace runtime creates default."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = FsSkillRepository(tmpdir)

            assert repo.workspace_runtime is not None

    def test_path_existing_skill(self):
        """Test getting path for existing skill."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / SKILL_FILE
            skill_file.write_text("---\nname: test-skill\ndescription: Test\n---\nBody")

            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)

            path = repo.path("test-skill")

            assert path == str(skill_dir)

    def test_path_nonexistent_skill(self):
        """Test getting path for nonexistent skill raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)

            with pytest.raises(ValueError, match="not found"):
                repo.path("nonexistent-skill")

    def test_summaries(self):
        """Test getting all skill summaries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / SKILL_FILE
            skill_file.write_text("---\nname: test-skill\ndescription: Test skill\n---\nBody")

            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)

            summaries = repo.summaries()

            assert len(summaries) == 1
            assert summaries[0].name == "test-skill"
            assert summaries[0].description == "Test skill"

    def test_summaries_empty(self):
        """Test getting summaries when no skills found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)

            summaries = repo.summaries()

            assert summaries == []

    def test_get_skill(self):
        """Test getting a full skill."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / SKILL_FILE
            skill_file.write_text("---\nname: test-skill\ndescription: Test\n---\nSkill body")
            (skill_dir / "doc1.md").write_text("Doc 1 content")

            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)

            skill = repo.get("test-skill")

            assert isinstance(skill, Skill)
            assert skill.summary.name == "test-skill"
            assert skill.body == "Skill body"
            assert len(skill.resources) == 1
            assert skill.resources[0].path == "doc1.md"

    def test_get_skill_nonexistent(self):
        """Test getting nonexistent skill raises ValueError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)

            with pytest.raises(ValueError, match="not found"):
                repo.get("nonexistent-skill")

    def test_get_skill_with_multiple_docs(self):
        """Test getting skill with multiple doc files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / SKILL_FILE
            skill_file.write_text("---\nname: test-skill\ndescription: Test\n---\nBody")
            (skill_dir / "doc1.md").write_text("Doc 1")
            (skill_dir / "doc2.txt").write_text("Doc 2")
            (skill_dir / "not_doc.py").write_text("Not a doc")

            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)

            skill = repo.get("test-skill")

            assert len(skill.resources) == 2
            assert any(r.path == "doc1.md" for r in skill.resources)
            assert any(r.path == "doc2.txt" for r in skill.resources)

    def test_parse_summary(self):
        """Test parsing skill summary."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_file = Path(tmpdir) / SKILL_FILE
            skill_file.write_text("---\nname: test-skill\ndescription: Test description\n---\nBody")
            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)
            front_matter, _ = repo._parse_yaml(str(skill_file))
            skill = Skill()
            repo._parse_summary(front_matter, skill)

            assert skill.summary.name == "test-skill"
            assert skill.summary.description == "Test description"

    def test_parse_summary_missing_name(self):
        """Test parsing summary with missing name."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_file = Path(tmpdir) / SKILL_FILE
            skill_file.write_text("---\ndescription: Test\n---\nBody")
            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)
            front_matter, _ = repo._parse_yaml(str(skill_file))
            skill = Skill()
            repo._parse_summary(front_matter, skill)

            assert skill.summary.name == ""

    def test_parse_full(self):
        """Test parsing full skill file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_file = Path(tmpdir) / SKILL_FILE
            skill_file.write_text("---\nname: test-skill\ndescription: Test\n---\nSkill body content")
            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)
            skill = Skill()
            repo._parse_all(str(skill_file), skill)

            assert skill.summary.name == "test-skill"
            assert skill.body == "Skill body content"
            assert skill.tools == []

    def test_from_markdown(self):
        """Test parsing markdown with front matter."""
        content = "---\nname: test-skill\ndescription: Test\n---\nBody content"
        front_matter, body = FsSkillRepository.from_markdown(content)

        assert front_matter["name"] == "test-skill"
        assert front_matter["description"] == "Test"
        assert body == "Body content"

    def test_from_markdown_no_front_matter(self):
        """Test parsing markdown without front matter raises ValueError."""
        content = "Just plain markdown"

        with pytest.raises(ValueError, match="must start with YAML frontmatter"):
            FsSkillRepository.from_markdown(content)

    def test_from_markdown_incomplete_front_matter(self):
        """Test parsing markdown with incomplete front matter raises ValueError."""
        content = "------\nname#test\n---\nBody content"

        with pytest.raises(ValueError, match="YAML frontmatter must be a dictionary"):
            FsSkillRepository.from_markdown(content)

    def test_from_markdown_invalid_yaml(self):
        """Test parsing markdown with invalid YAML raises ValueError."""
        content = "---\ninvalid: yaml: content: here\n---\nBody"

        with pytest.raises(ValueError, match="Invalid YAML"):
            FsSkillRepository.from_markdown(content)

    def test_is_doc_file(self):
        """Test checking if file is a doc file."""
        assert FsSkillRepository._is_doc_file("file.md") is True
        assert FsSkillRepository._is_doc_file("file.txt") is True
        assert FsSkillRepository._is_doc_file("file.MD") is True
        assert FsSkillRepository._is_doc_file("file.TXT") is True
        assert FsSkillRepository._is_doc_file("file.py") is False
        assert FsSkillRepository._is_doc_file("file.js") is False

    def test_scan_duplicate_roots(self):
        """Test scanning with duplicate roots."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = Path(tmpdir) / "test-skill"
            skill_dir.mkdir()
            skill_file = skill_dir / SKILL_FILE
            skill_file.write_text("---\nname: test-skill\ndescription: Test\n---\nBody")

            # Pass same root twice
            repo = FsSkillRepository(tmpdir, tmpdir, workspace_runtime=self.mock_runtime)

            # Should only find skill once
            summaries = repo.summaries()
            assert len(summaries) == 1

    def test_scan_empty_root(self):
        """Test scanning with empty root."""
        repo = FsSkillRepository("", workspace_runtime=self.mock_runtime)

        summaries = repo.summaries()

        assert summaries == []

    def test_scan_nested_directories(self):
        """Test scanning nested directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_dir = Path(tmpdir) / "level1" / "level2" / "test-skill"
            nested_dir.mkdir(parents=True)
            skill_file = nested_dir / SKILL_FILE
            skill_file.write_text("---\nname: test-skill\ndescription: Test\n---\nBody")

            repo = FsSkillRepository(tmpdir, workspace_runtime=self.mock_runtime)

            summaries = repo.summaries()

            assert len(summaries) == 1
            assert summaries[0].name == "test-skill"


class TestNewDefaultSkillRepository:
    """Test suite for create_default_skill_repository function."""

    def test_create_default_skill_repository(self):
        """Test creating default skill repository."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = create_default_skill_repository(tmpdir)

            assert isinstance(repo, FsSkillRepository)
            assert repo.workspace_runtime is not None

    def test_create_default_skill_repository_with_runtime(self):
        """Test creating repository with custom runtime."""
        mock_runtime = Mock(spec=BaseWorkspaceRuntime)
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = create_default_skill_repository(tmpdir, workspace_runtime=mock_runtime)

            assert repo.workspace_runtime == mock_runtime

