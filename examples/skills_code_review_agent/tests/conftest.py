"""Shared test fixtures for code review agent tests."""

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the example package is importable
_parent = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_parent))


@pytest.fixture
def fixtures_dir():
    """Path to the test fixtures directory."""
    return Path(__file__).resolve().parent.parent / "fixtures" / "diffs"


@pytest.fixture
def read_diff():
    """Helper to read a diff fixture file."""
    base = Path(__file__).resolve().parent.parent / "fixtures" / "diffs"

    def _read(name: str) -> str:
        path = base / name
        if not path.exists():
            raise FileNotFoundError(f"Fixture not found: {path}")
        return path.read_text(encoding="utf-8")

    return _read


@pytest.fixture
def temp_db():
    """Create a temporary SQLite database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup
    try:
        os.unlink(db_path)
    except OSError:
        pass
