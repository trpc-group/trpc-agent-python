# 测试覆盖度（Missing Tests）

## 检查项

### 1. 单元测试覆盖
- ❌ 新增功能无对应测试
- ❌ 测试覆盖率 < 80%
- ✅ 关键路径 100% 覆盖

### 2. 边界条件测试
- ❌ 只测试正常流程
- ❌ 缺少空值/异常输入测试
- ✅ 测试边界值和异常情况

### 3. 异步代码测试
- ❌ async 函数无对应测试
- ❌ 测试未等待异步操作
- ✅ 使用 pytest-asyncio 测试异步代码

### 4. 集成测试
- ❌ 只有单元测试，缺少集成测试
- ❌ 关键流程无端到端测试
- ✅ 多层次测试体系

## 示例代码

### ❌ 错误示例
```python
# 缺少测试的函数
def calculate_discount(price, user_level):
    if user_level == "VIP":
        return price * 0.8
    return price

# 测试只覆盖正常流程
def test_calculate_discount():
    assert calculate_discount(100, "VIP") == 80
    # 缺少：空值、负数、非预期等级的测试
```

### ✅ 正确示例
```python
# 完整的测试套件
def test_calculate_discount_vip():
    assert calculate_discount(100, "VIP") == 80

def test_calculate_discount_normal():
    assert calculate_discount(100, "NORMAL") == 100

def test_calculate_discount_negative_price():
    with pytest.raises(ValueError):
        calculate_discount(-100, "VIP")

def test_calculate_discount_invalid_level():
    assert calculate_discount(100, "INVALID") == 100

# 异步代码测试
@pytest.mark.asyncio
async def test_async_fetch():
    result = await async_fetch("https://api.example.com/data")
    assert result["status"] == "success"
```

## 检测方法
- 覆盖率工具：pytest-cov 生成覆盖率报告
- AST 分析：检查函数/类是否有对应的测试文件
- 启发式规则：新增代码必须有对应测试
- 文件命名：检查 `test_*.py` 或 `*_test.py` 的存在性
