# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
#!/usr/bin/env python3
"""Initialize the code review agent SQLite database.

Usage:
    python storage/init_db.py [--db-path review.db]

Creates all six tables with proper schema and foreign keys enabled.
Safe to run multiple times (uses IF NOT EXISTS).
"""

import argparse
import sys
from pathlib import Path

# Add parent to path for import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage.schema import ReviewStore


def main():
    parser = argparse.ArgumentParser(description='Initialize code review agent database')
    parser.add_argument('--db-path', type=str, default='review.db',
                        help='Path to SQLite database file (default: review.db)')
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if db_path.exists():
        print(f"Database already exists at {db_path}")
        print("Tables will be created if missing (CREATE TABLE IF NOT EXISTS)")
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Creating database at {db_path}")

    store = ReviewStore(str(db_path))

    tables = [
        r[0] for r in
        store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        if r[0] != 'sqlite_sequence'  # SQLite internal autoincrement helper, not a business table
    ]

    print(f"Tables created: {len(tables)}")
    for t in tables:
        print(f"  - {t}")

    print(f"\nForeign keys enabled: YES (PRAGMA foreign_keys = ON)")
    store.close()
    print("Database initialization complete.")


if __name__ == '__main__':
    main()
