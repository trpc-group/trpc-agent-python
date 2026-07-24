# 测试覆盖度详解（Test Coverage）

## 概述
测试覆盖度是衡量代码质量的重要指标，合理的测试策略能有效预防 Bug 和重构风险。

## 测试层次

### 1. 单元测试（Unit Tests）

**原则：**
- 测试单个函数/类的行为
- 快速执行（毫秒级）
- 无外部依赖（可 mock）

**示例：**

```python
# ✅ 良好的单元测试
def test_calculate_discount_vip():
    """测试 VIP 用户折扣"""
    result = calculate_discount(100, "VIP")
    assert result == 80

def test_calculate_discount_negative_price():
    """测试负价格异常"""
    with pytest.raises(ValueError):
        calculate_discount(-100, "VIP")

def test_calculate_discount_invalid_level():
    """测试无效用户等级"""
    result = calculate_discount(100, "INVALID")
    assert result == 100  # 默认无折扣
```

### 2. 集成测试（Integration Tests）

**原则：**
- 测试多个组件协同工作
- 包含真实依赖（数据库、网络）
- 执行时间较长（秒级）

**示例：**

```python
# ✅ 集成测试
@pytest.mark.integration
def test_user_registration_flow():
    """测试用户注册完整流程"""
    # 1. 提交注册表单
    response = client.post("/register", json={
        "username": "testuser",
        "email": "test@example.com",
        "password": "securepass"
    })
    assert response.status_code == 201

    # 2. 验证数据库记录
    user = db.query(User).filter_by(username="testuser").first()
    assert user is not None
    assert user.email == "test@example.com"

    # 3. 验证邮件发送
    assert len(mock_email.send_calls) == 1
```

### 3. 端到端测试（E2E Tests）

**原则：**
- 模拟真实用户操作
- 测试完整业务流程
- 执行时间最长（分钟级）

**示例：**

```python
# ✅ E2E 测试
@pytest.mark.e2e
def test_login_and_purchase_flow():
    """测试登录和购买流程"""
    # 1. 打开登录页面
    browser.get("https://example.com/login")

    # 2. 输入凭据并登录
    browser.find_element(By.ID, "username").send_keys("testuser")
    browser.find_element(By.ID, "password").send_keys("password")
    browser.find_element(By.ID, "login-btn").click()

    # 3. 验证登录成功
    assert "Welcome, testuser" in browser.page_source

    # 4. 浏览商品并购买
    browser.get("https://example.com/products/1")
    browser.find_element(By.ID, "add-to-cart").click()
    browser.find_element(By.ID, "checkout").click()

    # 5. 验证订单确认
    assert "Order confirmed" in browser.page_source
```

## 测试覆盖度策略

### 1. 代码覆盖率

**指标：**
- **行覆盖率**：执行的代码行比例
- **分支覆盖率**：条件分支的覆盖比例
- **路径覆盖率**：执行路径的覆盖比例

**工具：**
```bash
# 使用 pytest-cov 生成覆盖率报告
pytest --cov=src --cov-report=html --cov-report=term

# 目标：行覆盖率 > 80%，分支覆盖率 > 70%
```

### 2. 边界条件测试

**常见边界：**
- 空值/None
- 空字符串/空列表
- 极大值/极小值
- 特殊字符（unicode、控制字符）

**示例：**

```python
# ✅ 边界条件测试
def test_parse_email_empty():
    """测试空邮箱"""
    with pytest.raises(ValueError):
        parse_email("")

def test_parse_email_invalid_format():
    """测试无效邮箱格式"""
    with pytest.raises(ValueError):
        parse_email("not-an-email")

def test_parse_email_unicode():
    """测试 Unicode 字符"""
    result = parse_email("用户@例子.中国")
    assert result.username == "用户"
    assert result.domain == "例子.中国"

def test_parse_max_length():
    """测试最大长度输入"""
    long_string = "a" * 10000
    with pytest.raises(ValueError):
        parse_email(long_string + "@example.com")
```

### 3. 异常场景测试

**测试原则：**
- 每个异常分支都应有对应测试
- 测试异常处理逻辑是否正确
- 验证资源清理和错误恢复

**示例：**

```python
# ✅ 异常场景测试
def test_database_connection_failure():
    """测试数据库连接失败"""
    with mock.patch('db.connect') as mock_connect:
        mock_connect.side_effect = ConnectionError("Database unreachable")
        with pytest.raises(ConnectionError):
            get_user_data(1)

def test_timeout_handling():
    """测试超时处理"""
    with mock.patch('api.call') as mock_call:
        mock_call.side_effect = TimeoutError("Request timeout")
        result = fetch_data_with_retry("https://api.example.com/data")
        assert result is None  # 超时返回 None
```

## 测试质量指标

### 1. 测试独立性

**良好实践：**
```python
# ✅ 每个测试独立
def test_create_user():
    user = create_user("testuser")
    assert user.id is not None

def test_delete_user():
    user = create_user("testuser")  # 创建新用户，不依赖其他测试
    delete_user(user.id)
    assert get_user(user.id) is None
```

### 2. 测试可重复性

**良好实践：**
```python
# ✅ 使用固定种子或 mock
@pytest.fixture
def mock_random():
    with mock.patch('random.randint') as mock_randint:
        mock_randint.return_value = 42
        yield mock_randint

def test_generate_token(mock_random):
    token = generate_token()
    assert token == "fixed_token_42"  # 每次结果相同
```

### 3. 测试可读性

**良好实践：**
```python
# ✅ 清晰的测试命名和结构
def test_user_login_with_valid_credentials_should_return_token():
    """测试：有效凭据登录应返回令牌"""
    # Arrange: 准备测试数据
    username = "testuser"
    password = "validpass"

    # Act: 执行被测试的操作
    result = authenticate(username, password)

    # Assert: 验证结果
    assert result.success is True
    assert result.token is not None
```

## 检测规则

### 启发式规则

```python
# 检测未测试的函数
def find_untested_functions(source_dir, test_dir):
    """找出没有对应测试的函数"""
    source_files = glob(f"{source_dir}/**/*.py", recursive=True)
    test_files = glob(f"{test_dir}/**/test_*.py", recursive=True)

    tested_functions = set()
    for test_file in test_files:
        content = read_file(test_file)
        tested_functions.update(extract_tested_functions(content))

    for source_file in source_files:
        functions = extract_functions(source_file)
        for func in functions:
            if func.name not in tested_functions:
                report_issue(f"函数 {func.name} 缺少测试")
```

### 覆盖率阈值

```python
coverage_thresholds = {
    "line_coverage": 0.8,  # 80% 行覆盖率
    "branch_coverage": 0.7,  # 70% 分支覆盖率
    "new_code_coverage": 0.9,  # 新代码 90% 覆盖率
}
```

## 测试最佳实践

### 1. 测试命名

**良好实践：**
```python
# ✅ 清晰的测试命名
def test_add_positive_numbers():
    pass

def test_add_with_negative_number():
    pass

def test_add_with_zero():
    pass
```

### 2. 测试组织

**良好实践：**
```python
# ✅ 使用 fixture 共享测试数据
@pytest.fixture
def sample_user():
    return User(id=1, username="testuser", email="test@example.com")

def test_user_email(sample_user):
    assert "@" in sample_user.email

def test_user_age(sample_user):
    assert sample_user.age >= 0
```

### 3. Mock 使用

**良好实践：**
```python
# ✅ 只 mock 外部依赖
def test_service_call():
    with mock.patch('external_api.call') as mock_api:
        mock_api.return_value = {"status": "ok"}
        result = my_service.process_data()
        assert result is True

# ❌ 不要 mock 被测试的代码
def test_service_call_wrong():
    with mock.patch('my_service.process_data') as mock_process:  # 错误！
        mock_process.return_value = True
        assert my_service.process_data() is True  # 测试无意义
```

## 修复优先级

1. **Critical**：核心业务逻辑无测试
2. **High**：复杂算法、安全相关功能无测试
3. **Medium**：边界条件、异常场景无测试
4. **Low**：简单 getter/setter 无测试

## 参考资料
- Python Testing Best Practices
- pytest 官方文档
- Test-Driven Development with Python
