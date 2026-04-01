#!/usr/bin/env python3

# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Improved MySQL database access example using SqlStorage with better error handling."""

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from typing import Dict
from typing import Optional

from sqlalchemy import Column
from sqlalchemy import DateTime
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import Text
from sqlalchemy.exc import IntegrityError
from trpc_agent_sdk.storage import SqlCondition
from trpc_agent_sdk.storage import SqlKey
from trpc_agent_sdk.storage import SqlStorage
from trpc_agent_sdk.storage import StorageData


@dataclass
class MySQLConfig:
    """MySQL database configuration."""

    host: str = "localhost"
    port: int = 3306
    username: str = "root"
    password: str = "password"
    database: str = "test_db"
    charset: str = "utf8mb4"

    # Connection pool settings
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    pool_recycle: int = 3600

    # SQLAlchemy settings
    echo: bool = False
    echo_pool: bool = False

    def get_async_url(self) -> str:
        """Get async MySQL connection URL."""
        return f"mysql+aiomysql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}?charset={self.charset}"

    def get_sync_url(self) -> str:
        """Get sync MySQL connection URL."""
        return f"mysql+pymysql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}?charset={self.charset}"

    def get_engine_kwargs(self) -> Dict[str, Any]:
        """Get SQLAlchemy engine kwargs."""
        return {
            "echo": self.echo,
            "echo_pool": self.echo_pool,
            "pool_size": self.pool_size,
            "max_overflow": self.max_overflow,
            "pool_timeout": self.pool_timeout,
            "pool_recycle": self.pool_recycle,
        }

    @classmethod
    def from_env(cls) -> 'MySQLConfig':
        """Create config from environment variables."""
        return cls(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            username=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", "password"),
            database=os.getenv("MYSQL_DATABASE", "test_db"),
            charset=os.getenv("MYSQL_CHARSET", "utf8mb4"),
            pool_size=int(os.getenv("MYSQL_POOL_SIZE", "10")),
            max_overflow=int(os.getenv("MYSQL_MAX_OVERFLOW", "20")),
            echo=os.getenv("MYSQL_ECHO", "false").lower() == "true",
        )


@dataclass
class UserData(StorageData):
    """User data model for demonstration."""
    __tablename__ = 'users'

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    username: str = Column(String(50), unique=True, nullable=False)
    email: str = Column(String(100), nullable=False)
    full_name: str = Column(String(100), nullable=True)
    bio: str = Column(Text, nullable=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class MySQLExample:
    """Improved MySQL database example using SqlStorage with better error handling."""

    def __init__(self, db_url: str, is_async: bool = True):
        """Initialize MySQL example.

        Args:
            db_url: MySQL database URL (e.g., mysql+aiomysql://user:password@localhost/dbname)
            is_async: Whether to use async operations
        """
        self.storage = SqlStorage(
            is_async=is_async,
            db_url=db_url,
            echo=True,  # Enable SQL logging
            pool_size=10,
            max_overflow=20)
        self.is_async = is_async
        self.session_id = int(time.time())  # Unique session identifier

    async def initialize(self):
        """Initialize database engine and create tables."""
        print("Initializing database...")
        try:
            await self.storage.create_sql_engine()
            print("Database initialized successfully!")
        except Exception as e:
            print(f"Failed to initialize database: {e}")
            raise

    async def close(self):
        """Close database connection."""
        print("Closing database connection...")
        try:
            await self.storage.close()
            print("Database connection closed!")
        except Exception as e:
            print(f"Error closing database connection: {e}")

    async def cleanup_existing_data(self):
        """Clean up any existing test data before starting examples."""
        print("\n=== Cleaning Up Existing Test Data ===")

        user_key = SqlKey(key=(), storage_cls=UserData)

        async with self.storage.create_db_session() as session:
            # Clean up any existing test users
            cleanup_condition = SqlCondition(filters=[UserData.email.like('%example.com%')])
            existing_users = await self.storage.query(session, user_key, cleanup_condition)

            if existing_users:
                print(f"Found {len(existing_users)} existing test users, cleaning up...")
                for user in existing_users:
                    # Access attributes while in session context
                    username = user.username
                    email = user.email
                    print(f"  - Removing: {username} ({email})")

                await self.storage.delete(session, user_key, cleanup_condition)
                await self.storage.commit(session)
                print("Cleanup completed successfully")
            else:
                print("No existing test users found")

    async def create_user_with_retry(self,
                                     username: str,
                                     email: str,
                                     full_name: str,
                                     bio: str,
                                     max_retries: int = 3) -> Optional[int]:
        """Create a user with retry mechanism for handling duplicates."""
        for attempt in range(max_retries):
            try:
                # Add attempt number to make username unique
                unique_username = f"{username}_{self.session_id}_{attempt}" if attempt > 0 else f"{username}_{self.session_id}"
                unique_email = f"{username}.{self.session_id}.{attempt}@example.com" if attempt > 0 else f"{username}.{self.session_id}@example.com"

                user_data = UserData(username=unique_username, email=unique_email, full_name=full_name, bio=bio)

                async with self.storage.create_db_session() as session:
                    await self.storage.add(session, user_data)
                    await self.storage.commit(session)
                    await self.storage.refresh(session, user_data)

                    print(f"Created user: ID={user_data.id}, Username={user_data.username}")
                    return user_data.id

            except IntegrityError as e:
                print(f"Attempt {attempt + 1}: Integrity error - {e}")
                if attempt == max_retries - 1:
                    print(f"Failed to create user after {max_retries} attempts")
                    return None
                continue
            except Exception as e:
                print(f"Unexpected error creating user: {e}")
                return None

        return None

    async def create_user_example(self):
        """Example of creating a new user with improved error handling."""
        print("\n=== Creating User Example ===")

        user_id = await self.create_user_with_retry(username="john_doe",
                                                    email="john.doe@example.com",
                                                    full_name="John Doe",
                                                    bio="Software engineer passionate about Python development")

        if user_id:
            print(f"Successfully created user with ID: {user_id}")
        else:
            print("Failed to create user")

        return user_id

    async def get_user_example(self, user_id: int):
        """Example of getting a user by ID."""
        print(f"\n=== Getting User by ID: {user_id} ===")

        if not user_id:
            print("No user ID provided")
            return None

        user_key = SqlKey(key=(user_id, ), storage_cls=UserData)

        async with self.storage.create_db_session() as session:
            try:
                user = await self.storage.get(session, user_key)

                if user:
                    # Access all attributes while in session context
                    username = user.username
                    email = user.email
                    full_name = user.full_name
                    bio = user.bio
                    created_at = user.created_at

                    print(f"Found user: {username} ({email})")
                    print(f"Full name: {full_name}")
                    print(f"Bio: {bio}")
                    print(f"Created at: {created_at}")
                    return user
                else:
                    print("User not found!")
                    return None
            except Exception as e:
                print(f"Error getting user: {e}")
                return None

    async def query_users_example(self):
        """Example of querying users with conditions."""
        print("\n=== Querying Users Example ===")

        user_key = SqlKey(key=(), storage_cls=UserData)

        async with self.storage.create_db_session() as session:
            try:
                # Query all users
                print("1. Query all users:")
                all_users_condition = SqlCondition()
                all_users = await self.storage.query(session, user_key, all_users_condition)

                if all_users:
                    for user in all_users:
                        # Access attributes while in session context
                        username = user.username
                        email = user.email
                        print(f"  - {username}: {email}")
                else:
                    print("  No users found")

                # Query users with filter
                print("\n2. Query users with email containing 'example.com':")
                filtered_condition = SqlCondition(filters=[UserData.email.like('%example.com%')],
                                                  order_func=lambda: UserData.created_at.desc(),
                                                  limit=5)
                filtered_users = await self.storage.query(session, user_key, filtered_condition)

                if filtered_users:
                    for user in filtered_users:
                        # Access attributes while in session context
                        username = user.username
                        email = user.email
                        print(f"  - {username}: {email}")
                else:
                    print("  No filtered users found")

                return all_users

            except Exception as e:
                print(f"Error querying users: {e}")
                return []

    async def update_user_example(self, user_id: int):
        """Example of updating a user."""
        print(f"\n=== Updating User ID: {user_id} ===")

        if not user_id:
            print("No user ID provided")
            return None

        user_key = SqlKey(key=(user_id, ), storage_cls=UserData)

        async with self.storage.create_db_session() as session:
            user = await self.storage.get(session, user_key)

            if user:
                # Access user attributes while still in session context
                username = user.username
                old_bio = user.bio

                # Update user data
                user.bio = f"Updated bio: Senior Python developer with 5+ years experience (updated at {datetime.utcnow()})"
                user.updated_at = datetime.utcnow()

                await self.storage.commit(session)

                # Refresh to get updated data
                await self.storage.refresh(session, user)
                new_bio = user.bio

                print(f"Updated user {username}")
                print(f"Old bio: {old_bio}")
                print(f"New bio: {new_bio}")
                return user
            else:
                print("User not found for update!")
                return None

    async def delete_user_example(self, user_id: int):
        """Example of deleting a user."""
        print(f"\n=== Deleting User ID: {user_id} ===")

        if not user_id:
            print("No user ID provided")
            return

        user_key = SqlKey(key=(), storage_cls=UserData)
        delete_condition = SqlCondition(filters=[UserData.id == user_id])

        async with self.storage.create_db_session() as session:
            await self.storage.delete(session, user_key, delete_condition)
            await self.storage.commit(session)
            print(f"Deleted user with ID: {user_id}")

    async def batch_operations_example(self):
        """Example of batch operations with improved error handling."""
        print("\n=== Batch Operations Example ===")

        batch_users = []
        successful_creates = 0

        for i in range(1, 6):
            user_id = await self.create_user_with_retry(username=f"batch_user_{i}",
                                                        email=f"batch_user_{i}@example.com",
                                                        full_name=f"Batch User {i}",
                                                        bio=f"This is batch user number {i}")

            if user_id:
                batch_users.append(user_id)
                successful_creates += 1

        print(f"Successfully created {successful_creates} out of 5 batch users")
        return batch_users

    async def final_cleanup(self):
        """Clean up all test data created during this session."""
        print("\n=== Final Cleanup ===")

        user_key = SqlKey(key=(), storage_cls=UserData)

        async with self.storage.create_db_session() as session:
            # Clean up users created in this session
            cleanup_condition = SqlCondition(filters=[UserData.username.like(f'%_{self.session_id}_%')])
            session_users = await self.storage.query(session, user_key, cleanup_condition)

            if session_users:
                print(f"Cleaning up {len(session_users)} users from this session:")
                for user in session_users:
                    # Access attributes while in session context
                    username = user.username
                    print(f"  - {username}")

                await self.storage.delete(session, user_key, cleanup_condition)
                await self.storage.commit(session)
                print("Session cleanup completed")
            else:
                print("No session users found to clean up")


async def run_mysql_example():
    """Run the improved MySQL example with better error handling."""
    print("Improved MySQL Database Example using SqlStorage")
    print("=" * 60)

    # Configuration
    config = MySQLConfig(
        host="localhost",
        port=3306,
        username="root",
        password="",  # Update with your password
        database="dev_db",  # Update with your database
        echo=True,
        pool_size=5,
        max_overflow=10)

    mysql_url = config.get_async_url()
    print(f"Using database URL: {mysql_url}")

    example = MySQLExample(mysql_url, is_async=True)

    try:
        # Initialize database
        await example.initialize()

        # Clean up any existing test data
        await example.cleanup_existing_data()

        # Create a user
        user_id = await example.create_user_example()

        # Get the user
        await example.get_user_example(user_id)

        # Query users
        await example.query_users_example()

        # Update the user
        if user_id:
            await example.update_user_example(user_id)

        # Batch operations
        await example.batch_operations_example()

        # Query again to see all users
        await example.query_users_example()

        # Clean up session data (comment out if you want to keep test data)
        await example.final_cleanup()

        print("\n=== Improved MySQL Example Completed Successfully! ===")

    except Exception as e:
        print(f"Error during example execution: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Always close the connection
        await example.close()


if __name__ == "__main__":
    asyncio.run(run_mysql_example())
