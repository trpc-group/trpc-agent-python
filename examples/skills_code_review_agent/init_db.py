#!/usr/bin/env python3
import sys
import os
from examples.skills_code_review_agent.db import Base, ReviewDbRepository

def initialize_database(db_url: str = "sqlite:///review_agent.db"):
    """
    Initializes the database schema by creating all required tables.
    Also handles basic migration (dropping tables if requested via reset).
    """
    print(f"Initializing database schema at: {db_url}")
    
    # Check if we want a clean reset
    if "--reset" in sys.argv:
        print("Warning: Reset option detected. Dropping all existing tables...")
        # Get raw engine to drop
        repo = ReviewDbRepository(db_url)
        Base.metadata.drop_all(repo.engine)
        print("Tables dropped.")
        
    repo = ReviewDbRepository(db_url)
    print("Database tables successfully initialized/migrated.")
    print("Tables created:")
    for table_name in Base.metadata.tables.keys():
        print(f"  - {table_name}")

if __name__ == "__main__":
    db_path = "sqlite:///review_agent.db"
    initialize_database(db_path)
