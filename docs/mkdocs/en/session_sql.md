# SqlStorage Database Storage Usage Guide

This document provides a detailed introduction on how to use the `SqlStorage` class for database operations, including support for MySQL, PostgreSQL, SQLite, and other databases.

## Overview

`SqlStorage` is an async/sync database storage implementation based on SQLAlchemy, providing a unified interface for handling various SQL database operations.

## Core Components

### 1. SqlStorage Class
The primary storage class that provides database connection and operation interfaces.

### 2. Helper Classes
- `SqlKey`: Used to identify database query keys
- `SqlCondition`: Used to define query conditions
- `StorageData`: Base class for data models

## Prerequisites

### 1. Install Required Dependencies

```bash
# Core dependencies
pip install sqlalchemy

# MySQL support
pip install aiomysql PyMySQL

# PostgreSQL support
pip install asyncpg psycopg2

# SQLite support (built into Python)
# No additional installation required
```

### 2. Database Setup

#### MySQL Setup
```sql
-- Create database
CREATE DATABASE test_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- Create user (optional)
CREATE USER 'test_user'@'localhost' IDENTIFIED BY 'test_password';
GRANT ALL PRIVILEGES ON test_db.* TO 'test_user'@'localhost';
FLUSH PRIVILEGES;
```

#### PostgreSQL Setup
```sql
-- Create database
CREATE DATABASE test_db;

-- Create user (optional)
CREATE USER test_user WITH PASSWORD 'test_password';
GRANT ALL PRIVILEGES ON DATABASE test_db TO test_user;
```

---

## Basic Usage

### 1. Initialize SqlStorage

```python
from trpc_agent_sdk.storage import SqlStorage

# Async mode (recommended)
storage = SqlStorage(
    is_async=True,
    db_url="mysql+aiomysql://root:password@localhost/test_db",
    echo=True,  # Enable SQL logging
    pool_size=10,
    max_overflow=20
)

# Sync mode
storage = SqlStorage(
    is_async=False,
    db_url="mysql+pymysql://root:password@localhost/test_db",
    echo=True
)
```

### 2. Define Data Models

```python
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text
from trpc_agent_sdk.storage import StorageData

@dataclass
class UserData(StorageData):
    """User data model"""
    __tablename__ = 'users'

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    username: str = Column(String(50), unique=True, nullable=False)
    email: str = Column(String(100), nullable=False)
    full_name: str = Column(String(100), nullable=True)
    bio: str = Column(Text, nullable=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### 3. Basic Operation Example

```python
import asyncio
from trpc_agent_sdk.storage import SqlStorage, SqlKey, SqlCondition

async def basic_example():
    # Initialize storage
    storage = SqlStorage(
        is_async=True,
        db_url="mysql+aiomysql://root:password@localhost/test_db"
    )

    try:
        # Create database engine and tables
        await storage.create_sql_engine()

        # Use database session
        async with storage.create_db_session() as session:
            # Create new user
            user = UserData(
                username="john_doe",
                email="john@example.com",
                full_name="John Doe",
                bio="Software engineer"
            )

            # Add user
            await storage.add(session, user)
            await storage.commit(session)
            await storage.refresh(session, user)

            print(f"Created user with ID: {user.id}")

            # Get user
            user_key = SqlKey(key=(user.id,), storage_cls=UserData)
            retrieved_user = await storage.get(session, user_key)
            print(f"Retrieved user: {retrieved_user.username}")

            # Query users
            query_key = SqlKey(key=(), storage_cls=UserData)
            condition = SqlCondition(
                filters=[UserData.email.like('%@example.com')],
                order_func=lambda: UserData.created_at.desc(),
                limit=10
            )
            users = await storage.query(session, query_key, condition)
            print(f"Found {len(users)} users")

    finally:
        await storage.close()

# Run example
asyncio.run(basic_example())
```

### 4. Configuration Management

```python
import os
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class DatabaseConfig:
    """Database configuration class"""
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
        """Get async connection URL"""
        return f"mysql+aiomysql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}?charset={self.charset}"

    def get_sync_url(self) -> str:
        """Get sync connection URL"""
        return f"mysql+pymysql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}?charset={self.charset}"

    def get_engine_kwargs(self) -> Dict[str, Any]:
        """Get engine parameters"""
        return {
            "echo": self.echo,
            "echo_pool": self.echo_pool,
            "pool_size": self.pool_size,
            "max_overflow": self.max_overflow,
            "pool_timeout": self.pool_timeout,
            "pool_recycle": self.pool_recycle,
        }

    @classmethod
    def from_env(cls) -> 'DatabaseConfig':
        """Create configuration from environment variables"""
        return cls(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "3306")),
            username=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", "password"),
            database=os.getenv("DB_NAME", "test_db"),
            echo=os.getenv("DB_ECHO", "false").lower() == "true",
        )
```

## SqlStorage Interface Details

### 1. Core Interfaces

#### Database Engine Management
```python
# Create database engine and tables
await storage.create_sql_engine()

# Close database connection
await storage.close()
```

#### Session Management
```python
# Create database session (recommended to use context manager)
async with storage.create_db_session() as session:
    # Execute database operations here
    pass

# Create raw session (requires manual management)
session = await storage.create_sql_session()
```

### 2. CRUD Operations

#### Add Data
```python
async with storage.create_db_session() as session:
    user = UserData(username="test", email="test@example.com")
    await storage.add(session, user)
    await storage.commit(session)
    await storage.refresh(session, user)  # Get auto-generated ID
```

#### Get Data
```python
async with storage.create_db_session() as session:
    # Get by primary key
    user_key = SqlKey(key=(user_id,), storage_cls=UserData)
    user = await storage.get(session, user_key)
```

#### Query Data
```python
async with storage.create_db_session() as session:
    query_key = SqlKey(key=(), storage_cls=UserData)

    # Simple query
    condition = SqlCondition()
    all_users = await storage.query(session, query_key, condition)

    # Query with conditions
    condition = SqlCondition(
        filters=[
            UserData.email.like('%@example.com'),
            UserData.created_at > datetime(2024, 1, 1)
        ],
        order_func=lambda: UserData.created_at.desc(),
        limit=10
    )
    filtered_users = await storage.query(session, query_key, condition)
```

#### Delete Data
```python
async with storage.create_db_session() as session:
    delete_key = SqlKey(key=(), storage_cls=UserData)
    condition = SqlCondition(filters=[UserData.id == user_id])
    await storage.delete(session, delete_key, condition)
    await storage.commit(session)
```

#### Update Data
```python
async with storage.create_db_session() as session:
    user_key = SqlKey(key=(user_id,), storage_cls=UserData)
    user = await storage.get(session, user_key)

    if user:
        user.bio = "Updated bio"
        user.updated_at = datetime.utcnow()
        await storage.commit(session)
        await storage.refresh(session, user)
```

### 3. Advanced Features

#### Transaction Management
```python
async with storage.create_db_session() as session:
    try:
        # Execute multiple operations
        await storage.add(session, user1)
        await storage.add(session, user2)
        await storage.commit(session)
    except Exception as e:
        # Auto-rollback (handled by context manager)
        print(f"Transaction failed: {e}")
        raise
```

#### Batch Operations
```python
async with storage.create_db_session() as session:
    users = [
        UserData(username=f"user_{i}", email=f"user_{i}@example.com")
        for i in range(10)
    ]

    for user in users:
        await storage.add(session, user)

    await storage.commit(session)
```

#### Complex Query Conditions
```python
from sqlalchemy import and_, or_

condition = SqlCondition(
    filters=[
        and_(
            UserData.created_at > datetime(2024, 1, 1),
            or_(
                UserData.email.like('%@gmail.com'),
                UserData.email.like('%@yahoo.com')
            )
        )
    ],
    order_func=lambda: [UserData.created_at.desc(), UserData.username.asc()],
    limit=50
)
```

## Database Connection URLs

### MySQL
```python
# Async connection (recommended)
mysql_async_url = "mysql+aiomysql://username:password@host:port/database"

# Sync connection
mysql_sync_url = "mysql+pymysql://username:password@host:port/database"

# Connection with parameters
mysql_url = "mysql+aiomysql://user:pass@localhost/db?charset=utf8mb4&autocommit=true"

# SSL connection
mysql_ssl_url = "mysql+pymysql://user:pass@host/db?ssl_ca=/path/to/ca.pem"
```

### PostgreSQL
```python
# Async connection
postgres_async_url = "postgresql+asyncpg://username:password@host:port/database"

# Sync connection
postgres_sync_url = "postgresql+psycopg2://username:password@host:port/database"

# Connection with parameters
postgres_url = "postgresql+asyncpg://user:pass@localhost/db?ssl=require"
```

### SQLite
```python
# Async connection
sqlite_async_url = "sqlite+aiosqlite:///path/to/database.db"

# Sync connection
sqlite_sync_url = "sqlite:///path/to/database.db"

# In-memory database
sqlite_memory_url = "sqlite:///:memory:"
```

## Data Model Definitions

### Basic Model
```python
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from trpc_agent_sdk.storage import StorageData

@dataclass
class UserData(StorageData):
    """User data model"""
    __tablename__ = 'users'

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    username: str = Column(String(50), unique=True, nullable=False, index=True)
    email: str = Column(String(100), nullable=False, index=True)
    full_name: str = Column(String(100), nullable=True)
    bio: str = Column(Text, nullable=True)
    is_active: bool = Column(Boolean, default=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### Relational Model
```python
@dataclass
class PostData(StorageData):
    """Post data model"""
    __tablename__ = 'posts'

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    title: str = Column(String(200), nullable=False)
    content: str = Column(Text, nullable=False)
    user_id: int = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at: datetime = Column(DateTime, default=datetime.utcnow)

    # Relationship (optional)
    # user = relationship("UserData", back_populates="posts")

@dataclass
class TagData(StorageData):
    """Tag data model"""
    __tablename__ = 'tags'

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    name: str = Column(String(50), unique=True, nullable=False)
    description: str = Column(String(200), nullable=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
```

### Model Best Practices
```python
@dataclass
class BaseModel(StorageData):
    """Base model class"""
    __abstract__ = True  # Abstract base class, will not create a table

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

@dataclass
class ProductData(BaseModel):
    """Product data model"""
    __tablename__ = 'products'

    name: str = Column(String(100), nullable=False, index=True)
    price: int = Column(Integer, nullable=False)  # Stored in cents
    description: str = Column(Text, nullable=True)
    is_available: bool = Column(Boolean, default=True, index=True)
    category_id: int = Column(Integer, ForeignKey('categories.id'), nullable=True)
```

## Complete Usage Example

### Practical Application Example
```python
import asyncio
from datetime import datetime
from trpc_agent_sdk.storage import SqlStorage, SqlKey, SqlCondition

class UserService:
    """User service class example"""

    def __init__(self, db_url: str):
        self.storage = SqlStorage(
            is_async=True,
            db_url=db_url,
            echo=True,
            pool_size=10,
            max_overflow=20
        )

    async def initialize(self):
        """Initialize database"""
        await self.storage.create_sql_engine()

    async def create_user(self, username: str, email: str, full_name: str = None) -> int:
        """Create user"""
        async with self.storage.create_db_session() as session:
            user = UserData(
                username=username,
                email=email,
                full_name=full_name,
                created_at=datetime.utcnow()
            )

            await self.storage.add(session, user)
            await self.storage.commit(session)
            await self.storage.refresh(session, user)

            return user.id

    async def get_user_by_id(self, user_id: int) -> UserData:
        """Get user by ID"""
        async with self.storage.create_db_session() as session:
            user_key = SqlKey(key=(user_id,), storage_cls=UserData)
            return await self.storage.get(session, user_key)

    async def find_users_by_email_domain(self, domain: str, limit: int = 10) -> list:
        """Find users by email domain"""
        async with self.storage.create_db_session() as session:
            query_key = SqlKey(key=(), storage_cls=UserData)
            condition = SqlCondition(
                filters=[UserData.email.like(f'%@{domain}')],
                order_func=lambda: UserData.created_at.desc(),
                limit=limit
            )
            return await self.storage.query(session, query_key, condition)

    async def update_user_bio(self, user_id: int, bio: str) -> bool:
        """Update user bio"""
        async with self.storage.create_db_session() as session:
            user_key = SqlKey(key=(user_id,), storage_cls=UserData)
            user = await self.storage.get(session, user_key)

            if user:
                user.bio = bio
                user.updated_at = datetime.utcnow()
                await self.storage.commit(session)
                return True
            return False

    async def delete_inactive_users(self, days: int = 30) -> int:
        """Delete inactive users"""
        from datetime import timedelta
        cutoff_date = datetime.utcnow() - timedelta(days=days)

        async with self.storage.create_db_session() as session:
            delete_key = SqlKey(key=(), storage_cls=UserData)
            condition = SqlCondition(
                filters=[
                    UserData.is_active == False,
                    UserData.updated_at < cutoff_date
                ]
            )

            # Query users to be deleted first
            users_to_delete = await self.storage.query(session, delete_key, condition)
            count = len(users_to_delete)

            # Execute deletion
            await self.storage.delete(session, delete_key, condition)
            await self.storage.commit(session)

            return count

    async def close(self):
        """Close database connection"""
        await self.storage.close()

# Usage example
async def main():
    service = UserService("mysql+aiomysql://root:password@localhost/test_db")

    try:
        await service.initialize()

        # Create user
        user_id = await service.create_user("john_doe", "john@example.com", "John Doe")
        print(f"Created user with ID: {user_id}")

        # Get user
        user = await service.get_user_by_id(user_id)
        print(f"Retrieved user: {user.username}")

        # Find users
        users = await service.find_users_by_email_domain("example.com")
        print(f"Found {len(users)} users with example.com email")

        # Update user
        success = await service.update_user_bio(user_id, "Updated bio")
        print(f"Update successful: {success}")

    finally:
        await service.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## Error Handling and Best Practices

### 1. Exception Handling
```python
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

async def safe_create_user(storage, username: str, email: str):
    """Safe user creation example"""
    async with storage.create_db_session() as session:
        try:
            user = UserData(username=username, email=email)
            await storage.add(session, user)
            await storage.commit(session)
            await storage.refresh(session, user)
            return user.id

        except IntegrityError as e:
            print(f"Data integrity error (possibly duplicate data): {e}")
            return None

        except SQLAlchemyError as e:
            print(f"Database error: {e}")
            return None

        except Exception as e:
            print(f"Unknown error: {e}")
            return None
```

### 2. Connection Management
```python
class DatabaseManager:
    """Database manager"""

    def __init__(self, db_url: str):
        self.storage = None
        self.db_url = db_url

    async def __aenter__(self):
        self.storage = SqlStorage(is_async=True, db_url=self.db_url)
        await self.storage.create_sql_engine()
        return self.storage

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.storage:
            await self.storage.close()

# Usage
async def example_with_manager():
    async with DatabaseManager("mysql+aiomysql://...") as storage:
        async with storage.create_db_session() as session:
            # Execute database operations
            pass
```

### 3. Performance Optimization Recommendations

#### Connection Pool Configuration
```python
storage = SqlStorage(
    is_async=True,
    db_url=db_url,
    pool_size=20,          # Connection pool size
    max_overflow=30,       # Maximum overflow connections
    pool_timeout=30,       # Connection acquisition timeout
    pool_recycle=3600,     # Connection recycle time (seconds)
    pool_pre_ping=True,    # Pre-connection test
)
```

#### Batch Operations
```python
async def batch_create_users(storage, users_data: list):
    """Batch create users"""
    async with storage.create_db_session() as session:
        try:
            for user_data in users_data:
                user = UserData(**user_data)
                await storage.add(session, user)

            await storage.commit(session)
            print(f"Successfully created {len(users_data)} users")

        except Exception as e:
            print(f"Batch operation failed: {e}")
            # Session will auto-rollback
```

#### Query Optimization
```python
# Use indexed fields for queries
condition = SqlCondition(
    filters=[
        UserData.username == "john_doe",  # username has index
        UserData.is_active == True        # is_active has index
    ]
)

# Limit query result count
condition = SqlCondition(
    filters=[UserData.created_at > datetime(2024, 1, 1)],
    order_func=lambda: UserData.created_at.desc(),
    limit=100  # Limit result count
)
```

## Troubleshooting

### Common Errors and Solutions

1. **Connection Error**
   ```python
   # Error: Can't connect to MySQL server
   # Solution: Check database service status and connection parameters

   # Test connection
   try:
       await storage.create_sql_engine()
       print("Database connection successful")
   except Exception as e:
       print(f"Connection failed: {e}")
   ```

2. **Table Not Found Error**
   ```python
   # Error: Table 'database.table_name' doesn't exist
   # Solution: Ensure create_sql_engine() has been called

   await storage.create_sql_engine()  # This creates all tables
   ```

3. **Data Integrity Error**
   ```python
   # Error: Duplicate entry 'value' for key 'column_name'
   # Solution: Check unique constraint fields

   try:
       await storage.add(session, user)
       await storage.commit(session)
   except IntegrityError:
       print("Data already exists or violates constraint conditions")
   ```

### Debug Mode
```python
# Enable verbose logging
import logging
logging.basicConfig(level=logging.DEBUG)

storage = SqlStorage(
    is_async=True,
    db_url=db_url,
    echo=True,        # Display SQL statements
    echo_pool=True,   # Display connection pool info
)
```

## Running Examples

```bash
# 1. Install dependencies
pip install sqlalchemy aiomysql

# 2. Set environment variables
export DB_URL="mysql+aiomysql://root:password@localhost/test_db"

# 3. Run example
python examples/storage/sql_example.py

# 4. Use a different database
export DB_URL="postgresql+asyncpg://user:pass@localhost/test_db"
python examples/storage/sql_example.py
```

## Supported Databases

### PostgreSQL
- psycopg2
  ```txt
  Required package: pip install psycopg2-binary
  url: "postgresql+psycopg2://username:password@localhost:5432/mydb"
  ```
- asyncpg
  ```txt
  Required package: pip install asyncpg
  url: "postgresql+asyncpg://username:password@localhost:5432/mydb"
  ```
- pg8000
  ```txt
  Required package: pip install pg8000
  url: "postgresql+pg8000://username:password@localhost:5432/mydb"
  ```

###  MySQL/MariaDB
- PyMySQL
  ```txt
  Required package: pip install PyMySQL
  url: "mysql+pymysql://username:password@localhost:3306/mydb"
  ```
- mysqlclient
  ```txt
  Required package: pip install mysqlclient
  url: "mysql+mysqldb://username:password@localhost:3306/mydb"
  ```
- mysqlconnector
  ```txt
  Required package: pip install mysql-connector-python
  url: "mysql+mysqlconnector://username:password@localhost:3306/mydb"
  ```
- aiomysql
  ```txt
  Required package: pip install aiomysql
  url: "mysql+aiomysql://username:password@localhost:3306/mydb"
  ```

### SQLite
- sqlite3
  ```txt
  # Built-in, no installation required
  url: "sqlite:///./test.db"
  ```
- aiosqlite
  ```txt
  Required package: pip install aiosqlite
  url: "sqlite+aiosqlite:///./test.db"
  ```

### Oracle
- cx_Oracle
  ```txt
  Required package: pip install cx_Oracle
  url: "oracle+cx_oracle://username:password@localhost:1521/xe"
  ```
- oracledb
  ```txt
  Required package: pip install oracledb
  url: "oracle+oracledb://username:password@localhost:1521/xe"
  ```

### SQL Server
- pyodbc
  ```txt
  Required package: pip install pyodbc
  url: "mssql+pyodbc://username:password@server:1433/database?driver=ODBC+Driver+17+for+SQL+Server"
  ```
- pymssql
  ```txt
  Required package: pip install pymssql
  url: "mssql+pymssql://username:password@server:1433/database"
  ```
