# storage/migrations.py - 真迁移系统（记录已应用版本，支持后续 ALTER）
import sqlite3
from datetime import datetime

# 当前应用的迁移版本列表
APPLIED_VERSIONS = ["v1"]  # v1: 初始七表 schema

# 列迁移配置：后续版本新增列在此登记
# 格式: {version: [(table_name, column_name, column_type, default_value)]}
COLUMN_MIGRATIONS = {
    # 示例（待后续版本添加）：
    # "v2": [
    #     ("review_tasks", "author", "TEXT", "unknown"),
    #     ("findings", "false_positive", "INTEGER", "0")
    # ]
}


def _get_applied_versions(conn: sqlite3.Connection) -> set[str]:
    """获取已应用的迁移版本集合"""
    cursor = conn.cursor()
    cursor.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def _apply_version(conn: sqlite3.Connection, version: str):
    """记录迁移版本为已应用"""
    cursor = conn.cursor()
    cursor.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                   (version, datetime.utcnow().isoformat()))


def _add_column_if_not_exists(conn: sqlite3.Connection,
                              table: str,
                              column: str,
                              col_type: str,
                              default_value: any = None):
    """添加列（幂等：先检查列是否存在）"""
    cursor = conn.cursor()

    # 检查列是否已存在
    cursor.execute(f"PRAGMA table_info({table})")
    existing_columns = {row[1] for row in cursor.fetchall()}

    if column in existing_columns:
        return  # 列已存在，跳过

    # 构造 ALTER TABLE 语句
    alter_sql = f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
    if default_value is not None:
        alter_sql += f" DEFAULT {default_value}"

    cursor.execute(alter_sql)


def _migrate_column_upgrades(conn: sqlite3.Connection, version: str):
    """应用列迁移（升级：新增列）"""
    if version not in COLUMN_MIGRATIONS:
        return

    migrations = COLUMN_MIGRATIONS[version]
    for table, column, col_type, default_value in migrations:
        _add_column_if_not_exists(conn, table, column, col_type, default_value)


def run_migrations(conn: sqlite3.Connection):
    """运行所有待应用的迁移

    Args:
        conn: SQLite 数据库连接

    迁移策略：
    1. 确保 schema_migrations 表存在
    2. 查询已应用的版本
    3. 对未应用的版本：
       - 执行列迁移（ALTER TABLE）
       - 记录版本为已应用
    """
    # 1. 确保版本表存在
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                 "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")

    # 2. 获取已应用版本
    applied = _get_applied_versions(conn)

    # 3. 应用未执行的迁移版本
    for version in APPLIED_VERSIONS:
        if version not in applied:
            # 执行列迁移
            _migrate_column_upgrades(conn, version)

            # 记录版本为已应用
            _apply_version(conn, version)
            conn.commit()
