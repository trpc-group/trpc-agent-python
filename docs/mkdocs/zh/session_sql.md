# SqlStorage 数据库存储使用指南

本文档详细介绍了如何使用 `SqlStorage` 类进行数据库操作，包括 MySQL、PostgreSQL、SQLite 等数据库的支持。

## 概述

`SqlStorage` 是一个基于 SQLAlchemy 的异步/同步数据库存储实现，提供了统一的接口来处理各种 SQL 数据库操作。

## 核心组件

### 1. SqlStorage 类
主要的存储类，提供数据库连接和操作接口。

### 2. 辅助类
- `SqlKey`: 用于标识数据库查询的键
- `SqlCondition`: 用于定义查询条件
- `StorageData`: 数据模型基类

## 前置条件

### 1. 安装必需的依赖

```bash
# 核心依赖
pip install sqlalchemy

# MySQL 支持
pip install aiomysql PyMySQL

# PostgreSQL 支持
pip install asyncpg psycopg2

# SQLite 支持（Python 内置）
# 无需额外安装
```





### 2. 数据库设置

#### MySQL 设置
```sql
-- 创建数据库
CREATE DATABASE test_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 创建用户（可选）
CREATE USER 'test_user'@'localhost' IDENTIFIED BY 'test_password';
GRANT ALL PRIVILEGES ON test_db.* TO 'test_user'@'localhost';
FLUSH PRIVILEGES;
```

#### PostgreSQL 设置
```sql
-- 创建数据库
CREATE DATABASE test_db;

-- 创建用户（可选）
CREATE USER test_user WITH PASSWORD 'test_password';
GRANT ALL PRIVILEGES ON DATABASE test_db TO test_user;
```

---

## 基本使用方法

### 1. 初始化 SqlStorage

```python
from trpc_agent.storage import SqlStorage

# 异步模式（推荐）
storage = SqlStorage(
    is_async=True,
    db_url="mysql+aiomysql://root:password@localhost/test_db",
    echo=True,  # 启用 SQL 日志
    pool_size=10,
    max_overflow=20
)

# 同步模式
storage = SqlStorage(
    is_async=False,
    db_url="mysql+pymysql://root:password@localhost/test_db",
    echo=True
)
```

### 2. 定义数据模型

```python
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text
from trpc_agent.storage import StorageData

@dataclass
class UserData(StorageData):
    """用户数据模型"""
    __tablename__ = 'users'

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    username: str = Column(String(50), unique=True, nullable=False)
    email: str = Column(String(100), nullable=False)
    full_name: str = Column(String(100), nullable=True)
    bio: str = Column(Text, nullable=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

### 3. 基本操作示例

```python
import asyncio
from trpc_agent.storage import SqlStorage, SqlKey, SqlCondition

async def basic_example():
    # 初始化存储
    storage = SqlStorage(
        is_async=True,
        db_url="mysql+aiomysql://root:password@localhost/test_db"
    )

    try:
        # 创建数据库引擎和表
        await storage.create_sql_engine()

        # 使用数据库会话
        async with storage.create_db_session() as session:
            # 创建新用户
            user = UserData(
                username="john_doe",
                email="john@example.com",
                full_name="John Doe",
                bio="Software engineer"
            )

            # 添加用户
            await storage.add(session, user)
            await storage.commit(session)
            await storage.refresh(session, user)

            print(f"Created user with ID: {user.id}")

            # 获取用户
            user_key = SqlKey(key=(user.id,), storage_cls=UserData)
            retrieved_user = await storage.get(session, user_key)
            print(f"Retrieved user: {retrieved_user.username}")

            # 查询用户
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

# 运行示例
asyncio.run(basic_example())
```

### 4. 配置管理

```python
import os
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class DatabaseConfig:
    """数据库配置类"""
    host: str = "localhost"
    port: int = 3306
    username: str = "root"
    password: str = "password"
    database: str = "test_db"
    charset: str = "utf8mb4"

    # 连接池设置
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    pool_recycle: int = 3600

    # SQLAlchemy 设置
    echo: bool = False
    echo_pool: bool = False

    def get_async_url(self) -> str:
        """获取异步连接 URL"""
        return f"mysql+aiomysql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}?charset={self.charset}"

    def get_sync_url(self) -> str:
        """获取同步连接 URL"""
        return f"mysql+pymysql://{self.username}:{self.password}@{self.host}:{self.port}/{self.database}?charset={self.charset}"

    def get_engine_kwargs(self) -> Dict[str, Any]:
        """获取引擎参数"""
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
        """从环境变量创建配置"""
        return cls(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "3306")),
            username=os.getenv("DB_USER", "root"),
            password=os.getenv("DB_PASSWORD", "password"),
            database=os.getenv("DB_NAME", "test_db"),
            echo=os.getenv("DB_ECHO", "false").lower() == "true",
        )
```

## SqlStorage 接口详解

### 1. 核心接口

#### 数据库引擎管理
```python
# 创建数据库引擎和表
await storage.create_sql_engine()

# 关闭数据库连接
await storage.close()
```

#### 会话管理
```python
# 创建数据库会话（推荐使用上下文管理器）
async with storage.create_db_session() as session:
    # 在这里执行数据库操作
    pass

# 创建原始会话（需要手动管理）
session = await storage.create_sql_session()
```

### 2. CRUD 操作

#### 添加数据
```python
async with storage.create_db_session() as session:
    user = UserData(username="test", email="test@example.com")
    await storage.add(session, user)
    await storage.commit(session)
    await storage.refresh(session, user)  # 获取自动生成的 ID
```

#### 获取数据
```python
async with storage.create_db_session() as session:
    # 通过主键获取
    user_key = SqlKey(key=(user_id,), storage_cls=UserData)
    user = await storage.get(session, user_key)
```

#### 查询数据
```python
async with storage.create_db_session() as session:
    query_key = SqlKey(key=(), storage_cls=UserData)

    # 简单查询
    condition = SqlCondition()
    all_users = await storage.query(session, query_key, condition)

    # 带条件查询
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

#### 删除数据
```python
async with storage.create_db_session() as session:
    delete_key = SqlKey(key=(), storage_cls=UserData)
    condition = SqlCondition(filters=[UserData.id == user_id])
    await storage.delete(session, delete_key, condition)
    await storage.commit(session)
```

#### 更新数据
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

### 3. 高级功能

#### 事务管理
```python
async with storage.create_db_session() as session:
    try:
        # 执行多个操作
        await storage.add(session, user1)
        await storage.add(session, user2)
        await storage.commit(session)
    except Exception as e:
        # 自动回滚（由上下文管理器处理）
        print(f"Transaction failed: {e}")
        raise
```

#### 批量操作
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

#### 复杂查询条件
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

## 数据库连接 URL

### MySQL
```python
# 异步连接（推荐）
mysql_async_url = "mysql+aiomysql://username:password@host:port/database"

# 同步连接
mysql_sync_url = "mysql+pymysql://username:password@host:port/database"

# 带参数的连接
mysql_url = "mysql+aiomysql://user:pass@localhost/db?charset=utf8mb4&autocommit=true"

# SSL 连接
mysql_ssl_url = "mysql+pymysql://user:pass@host/db?ssl_ca=/path/to/ca.pem"
```

### PostgreSQL
```python
# 异步连接
postgres_async_url = "postgresql+asyncpg://username:password@host:port/database"

# 同步连接
postgres_sync_url = "postgresql+psycopg2://username:password@host:port/database"

# 带参数的连接
postgres_url = "postgresql+asyncpg://user:pass@localhost/db?ssl=require"
```

### SQLite
```python
# 异步连接
sqlite_async_url = "sqlite+aiosqlite:///path/to/database.db"

# 同步连接
sqlite_sync_url = "sqlite:///path/to/database.db"

# 内存数据库
sqlite_memory_url = "sqlite:///:memory:"
```

## 数据模型定义

### 基本模型
```python
from dataclasses import dataclass
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from trpc_agent.storage import StorageData

@dataclass
class UserData(StorageData):
    """用户数据模型"""
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

### 关联模型
```python
@dataclass
class PostData(StorageData):
    """文章数据模型"""
    __tablename__ = 'posts'

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    title: str = Column(String(200), nullable=False)
    content: str = Column(Text, nullable=False)
    user_id: int = Column(Integer, ForeignKey('users.id'), nullable=False)
    created_at: datetime = Column(DateTime, default=datetime.utcnow)

    # 关联关系（可选）
    # user = relationship("UserData", back_populates="posts")

@dataclass
class TagData(StorageData):
    """标签数据模型"""
    __tablename__ = 'tags'

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    name: str = Column(String(50), unique=True, nullable=False)
    description: str = Column(String(200), nullable=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow)
```

### 模型最佳实践
```python
@dataclass
class BaseModel(StorageData):
    """基础模型类"""
    __abstract__ = True  # 抽象基类，不会创建表

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    created_at: datetime = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: datetime = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

@dataclass
class ProductData(BaseModel):
    """产品数据模型"""
    __tablename__ = 'products'

    name: str = Column(String(100), nullable=False, index=True)
    price: int = Column(Integer, nullable=False)  # 以分为单位存储
    description: str = Column(Text, nullable=True)
    is_available: bool = Column(Boolean, default=True, index=True)
    category_id: int = Column(Integer, ForeignKey('categories.id'), nullable=True)
```

## 完整使用示例

### 实际应用示例
```python
import asyncio
from datetime import datetime
from trpc_agent.storage import SqlStorage, SqlKey, SqlCondition

class UserService:
    """用户服务类示例"""

    def __init__(self, db_url: str):
        self.storage = SqlStorage(
            is_async=True,
            db_url=db_url,
            echo=True,
            pool_size=10,
            max_overflow=20
        )

    async def initialize(self):
        """初始化数据库"""
        await self.storage.create_sql_engine()

    async def create_user(self, username: str, email: str, full_name: str = None) -> int:
        """创建用户"""
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
        """根据ID获取用户"""
        async with self.storage.create_db_session() as session:
            user_key = SqlKey(key=(user_id,), storage_cls=UserData)
            return await self.storage.get(session, user_key)

    async def find_users_by_email_domain(self, domain: str, limit: int = 10) -> list:
        """根据邮箱域名查找用户"""
        async with self.storage.create_db_session() as session:
            query_key = SqlKey(key=(), storage_cls=UserData)
            condition = SqlCondition(
                filters=[UserData.email.like(f'%@{domain}')],
                order_func=lambda: UserData.created_at.desc(),
                limit=limit
            )
            return await self.storage.query(session, query_key, condition)

    async def update_user_bio(self, user_id: int, bio: str) -> bool:
        """更新用户简介"""
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
        """删除非活跃用户"""
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

            # 先查询要删除的用户数量
            users_to_delete = await self.storage.query(session, delete_key, condition)
            count = len(users_to_delete)

            # 执行删除
            await self.storage.delete(session, delete_key, condition)
            await self.storage.commit(session)

            return count

    async def close(self):
        """关闭数据库连接"""
        await self.storage.close()

# 使用示例
async def main():
    service = UserService("mysql+aiomysql://root:password@localhost/test_db")

    try:
        await service.initialize()

        # 创建用户
        user_id = await service.create_user("john_doe", "john@example.com", "John Doe")
        print(f"Created user with ID: {user_id}")

        # 获取用户
        user = await service.get_user_by_id(user_id)
        print(f"Retrieved user: {user.username}")

        # 查找用户
        users = await service.find_users_by_email_domain("example.com")
        print(f"Found {len(users)} users with example.com email")

        # 更新用户
        success = await service.update_user_bio(user_id, "Updated bio")
        print(f"Update successful: {success}")

    finally:
        await service.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## 错误处理和最佳实践

### 1. 异常处理
```python
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

async def safe_create_user(storage, username: str, email: str):
    """安全创建用户的示例"""
    async with storage.create_db_session() as session:
        try:
            user = UserData(username=username, email=email)
            await storage.add(session, user)
            await storage.commit(session)
            await storage.refresh(session, user)
            return user.id

        except IntegrityError as e:
            print(f"数据完整性错误（可能是重复数据）: {e}")
            return None

        except SQLAlchemyError as e:
            print(f"数据库错误: {e}")
            return None

        except Exception as e:
            print(f"未知错误: {e}")
            return None
```

### 2. 连接管理
```python
class DatabaseManager:
    """数据库管理器"""

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

# 使用方式
async def example_with_manager():
    async with DatabaseManager("mysql+aiomysql://...") as storage:
        async with storage.create_db_session() as session:
            # 执行数据库操作
            pass
```

### 3. 性能优化建议

#### 连接池配置
```python
storage = SqlStorage(
    is_async=True,
    db_url=db_url,
    pool_size=20,          # 连接池大小
    max_overflow=30,       # 最大溢出连接数
    pool_timeout=30,       # 获取连接超时时间
    pool_recycle=3600,     # 连接回收时间（秒）
    pool_pre_ping=True,    # 连接前测试
)
```

#### 批量操作
```python
async def batch_create_users(storage, users_data: list):
    """批量创建用户"""
    async with storage.create_db_session() as session:
        try:
            for user_data in users_data:
                user = UserData(**user_data)
                await storage.add(session, user)

            await storage.commit(session)
            print(f"Successfully created {len(users_data)} users")

        except Exception as e:
            print(f"Batch operation failed: {e}")
            # 会话会自动回滚
```

#### 查询优化
```python
# 使用索引字段进行查询
condition = SqlCondition(
    filters=[
        UserData.username == "john_doe",  # username 有索引
        UserData.is_active == True        # is_active 有索引
    ]
)

# 限制查询结果数量
condition = SqlCondition(
    filters=[UserData.created_at > datetime(2024, 1, 1)],
    order_func=lambda: UserData.created_at.desc(),
    limit=100  # 限制结果数量
)
```

## 故障排除

### 常见错误及解决方案

1. **连接错误**
   ```python
   # 错误: Can't connect to MySQL server
   # 解决: 检查数据库服务状态和连接参数

   # 测试连接
   try:
       await storage.create_sql_engine()
       print("数据库连接成功")
   except Exception as e:
       print(f"连接失败: {e}")
   ```

2. **表不存在错误**
   ```python
   # 错误: Table 'database.table_name' doesn't exist
   # 解决: 确保调用了 create_sql_engine()

   await storage.create_sql_engine()  # 这会创建所有表
   ```

3. **数据完整性错误**
   ```python
   # 错误: Duplicate entry 'value' for key 'column_name'
   # 解决: 检查唯一约束字段

   try:
       await storage.add(session, user)
       await storage.commit(session)
   except IntegrityError:
       print("数据已存在或违反约束条件")
   ```

### 调试模式
```python
# 启用详细日志
import logging
logging.basicConfig(level=logging.DEBUG)

storage = SqlStorage(
    is_async=True,
    db_url=db_url,
    echo=True,        # 显示 SQL 语句
    echo_pool=True,   # 显示连接池信息
)
```

## 运行示例

```bash
# 1. 安装依赖
pip install sqlalchemy aiomysql

# 2. 设置环境变量
export DB_URL="mysql+aiomysql://root:password@localhost/test_db"

# 3. 运行示例
python examples/storage/sql_example.py

# 4. 使用不同数据库
export DB_URL="postgresql+asyncpg://user:pass@localhost/test_db"
python examples/storage/sql_example.py
```

## 可以支持的数据库如下

### PostgreSQL
- psycopg2
  ```txt
  必须安装包：pip install psycopg2-binary
  url: "postgresql+psycopg2://username:password@localhost:5432/mydb"
  ```
- asyncpg
  ```txt
  必须安装包：pip install asyncpg
  url: "postgresql+asyncpg://username:password@localhost:5432/mydb"
  ```
- pg8000
  ```txt
  必须安装包：pip install pg8000
  url: "postgresql+pg8000://username:password@localhost:5432/mydb"
  ```

###  MySQL/MariaDB
- PyMySQL
  ```txt
  必须安装包：pip install PyMySQL
  url: "mysql+pymysql://username:password@localhost:3306/mydb"
  ```
- mysqlclient
  ```txt
  必须安装包：pip install mysqlclient
  url: "mysql+mysqldb://username:password@localhost:3306/mydb"
  ```
- mysqlconnector
  ```txt
  必须安装包：pip install mysql-connector-python
  url: "mysql+mysqlconnector://username:password@localhost:3306/mydb"
  ```
- aiomysql
  ```txt
  必须安装包：pip install aiomysql
  url: "mysql+aiomysql://username:password@localhost:3306/mydb"
  ```

### SQLite
- sqlite3
  ```txt
  # 系统自带，无需安装
  url: "sqlite:///./test.db"
  ```
- aiosqlite
  ```txt
  必须安装包：pip install aiosqlite
  url: "sqlite+aiosqlite:///./test.db"
  ```

### Oracle
- cx_Oracle
  ```txt
  必须安装包：pip install cx_Oracle
  url: "oracle+cx_oracle://username:password@localhost:1521/xe"
  ```
- oracledb
  ```txt
  必须安装包：pip install oracledb
  url: "oracle+oracledb://username:password@localhost:1521/xe"
  ```

### SQL Server
- pyodbc
  ```txt
  必须安装包：pip install pyodbc
  url: "mssql+pyodbc://username:password@server:1433/database?driver=ODBC+Driver+17+for+SQL+Server"
  ```
- pymssql
  ```txt
  必须安装包：pip install pymssql
  url: "mssql+pymssql://username:password@server:1433/database"
  ```
