"""Code review tools: review code, save results to database."""
import sqlite3
import json
from datetime import datetime

DB_PATH = "code_reviews.db"


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT,
            summary TEXT,
            bugs TEXT,
            improvements TEXT,
            score INTEGER,
            created_at TEXT
        )
    """)
    return conn


def review_code(code: str, file_path: str = "") -> dict:
    """Analyze code and return structured review.

    This is a tool the agent calls — the actual LLM analysis happens
    through the agent's model.  The returned dict is the structured
    output template.
    """
    return {
        "code_snippet": code[:500],
        "file_path": file_path,
        "needs_review": True,
    }


def save_review(file_path: str, summary: str, bugs: str,
                improvements: str, score: int = 0) -> str:
    """Save a code review to the SQLite database.

    Args:
        file_path: Path to the reviewed file
        summary: One-sentence summary
        bugs: Bug findings
        improvements: Suggested improvements
        score: Quality score (0-10)
    """
    conn = _get_db()
    conn.execute(
        "INSERT INTO reviews (file_path, summary, bugs, improvements, score, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (file_path, summary, bugs, improvements, score, datetime.now().isoformat())
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
    conn.close()
    return f"Review saved. Total reviews in database: {count}"
