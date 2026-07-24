# 敏感信息检测规则

## 概述

检测代码中硬编码的敏感信息，包括 API Key、Token、密码、证书、数据库连接字符串等。防止敏感信息泄露到代码仓库。

## 规则列表

### SD-01: API Key 硬编码

**严重级别**: Critical

**描述**: 在代码中硬编码 API Key 或 API Secret，可能导致密钥泄露。

**检测模式**:
- `API_KEY = "sk-..."` — OpenAI/第三方 API Key
- `api_secret = "..."` — API Secret
- `apikey = "..."` — 其他 API Key 格式

**正则匹配**: `(?i)(?:api_key|api[_-]?key|apikey)\s*[=:]\s*['\"](sk-[a-zA-Z0-9]{10,})['\"]`

### SD-02: 密码硬编码

**严重级别**: Critical

**描述**: 在代码中硬编码密码或口令，可能导致凭据泄露。

**检测模式**:
- `PASSWORD = "SuperSecret!"` — 明文密码
- `passwd = "123456"` — 明文口令
- `pwd = "admin"` — 管理员密码

**正则匹配**: `(?i)(?:password|passwd|pwd)\s*[=:]\s*['\"][^'"]{4,}['\"]`

### SD-03: GitHub Token 泄露

**严重级别**: Critical

**描述**: 在代码中硬编码 GitHub Personal Access Token，可能导致仓库被非法访问。

**检测模式**:
- `ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` — GitHub PAT
- `ghs_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` — GitHub OAuth Token

**正则匹配**: `ghp_[a-zA-Z0-9]{36,}`

### SD-04: AWS 凭证泄露

**严重级别**: Critical

**描述**: 在代码中硬编码 AWS Access Key，可能导致云资源被非法访问。

**检测模式**:
- `AKIAIOSFODNN7EXAMPLE` — AWS Access Key ID
- `AWS_SECRET_ACCESS_KEY = "..."` — AWS Secret Key

**正则匹配**: `AKIA[0-9A-Z]{16}`

### SD-05: 私钥硬编码

**严重级别**: Critical

**描述**: 在代码中硬编码 RSA/EC 私钥，可能导致加密体系被攻破。

**检测模式**:
- `-----BEGIN RSA PRIVATE KEY-----` — RSA 私钥
- `-----BEGIN EC PRIVATE KEY-----` — EC 私钥
- `-----BEGIN PRIVATE KEY-----` — 通用私钥

**正则匹配**: `-----BEGIN (?:RSA |EC )?PRIVATE KEY-----`

### SD-06: JWT Token 硬编码

**严重级别**: Critical

**描述**: 在代码中硬编码 JWT Token，可能导致身份认证被绕过。

**检测模式**:
- `eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdef...` — JWT Token

**正则匹配**: `eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}`

### SD-07: 数据库连接字符串包含密码

**严重级别**: Critical

**描述**: 数据库连接字符串中包含明文密码，可能导致数据库被非法访问。

**检测模式**:
- `postgres://admin:secret123@db.example.com:5432/prod` — PostgreSQL 连接串
- `mysql://user:password@host:3306/db` — MySQL 连接串
- `redis://:password@host:6379/0` — Redis 连接串

**正则匹配**: `(?:postgres(?:ql)?|mysql|redis)://[^:]+:[^@]+@`