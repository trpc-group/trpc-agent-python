"""电商购物助手工具函数。

提供 6 个电商场景的工具函数，供 agent 调用：

  - search_products(query, category)  — 搜索商品，支持按分类过滤
  - get_product_details(product_id)   — 获取商品详细信息（价格、评分、库存等）
  - add_to_cart(product_id, quantity) — 添加商品到购物车
  - check_order_status(order_id)      — 查询订单状态
  - get_cart()                        — 查看购物车内容
  - apply_coupon(code)                — 应用优惠券

每个工具函数返回字典格式的结果，包含成功/失败标志和相关信息。
所有商品和订单数据为演示用途的静态数据。
"""

from typing import Optional


def search_products(query: str, category: Optional[str] = None) -> dict:
    """搜索商品：按关键词和可选分类过滤。

    Args:
        query: 商品名称或描述的关键词（不区分大小写）。
        category: 可选的分类过滤（electronics, clothing, home）。

    Returns:
        {"query": ..., "category": ..., "total": N, "products": [...]}
    """
    # 静态商品目录（10 个商品）
    products = [
        {"id": "P001", "name": "Wireless Bluetooth Headphones", "category": "electronics", "price": 299.00, "rating": 4.5, "stock": 120},
        {"id": "P002", "name": "iPhone 15 Pro", "category": "electronics", "price": 7999.00, "rating": 4.8, "stock": 50},
        {"id": "P003", "name": "MacBook Pro 14", "category": "electronics", "price": 14999.00, "rating": 4.7, "stock": 30},
        {"id": "P004", "name": "Men's Running Shoes", "category": "clothing", "price": 599.00, "rating": 4.3, "stock": 200},
        {"id": "P005", "name": "Coffee Maker", "category": "home", "price": 1299.00, "rating": 4.6, "stock": 80},
        {"id": "P006", "name": "Sony WH-1000XM6 Headphones", "category": "electronics", "price": 2499.00, "rating": 4.9, "stock": 15},
        {"id": "P007", "name": "Samsung Galaxy S25", "category": "electronics", "price": 6999.00, "rating": 4.6, "stock": 60},
        {"id": "P008", "name": "Levi's 501 Jeans", "category": "clothing", "price": 499.00, "rating": 4.4, "stock": 300},
        {"id": "P009", "name": "Dyson V15 Vacuum", "category": "home", "price": 4599.00, "rating": 4.8, "stock": 25},
        {"id": "P010", "name": "Air Fryer XL", "category": "home", "price": 899.00, "rating": 4.2, "stock": 100},
    ]

    query_lower = query.lower()
    results = []
    for p in products:
        # 关键词模糊匹配
        if query_lower in p["name"].lower():
            # 按分类过滤（如果指定）
            if category is None or p["category"] == category:
                results.append(p)

    return {"query": query, "category": category, "total": len(results), "products": results}


def get_product_details(product_id: str) -> dict:
    """获取商品详细信息。

    Args:
        product_id: 商品 ID（如 P001）。

    Returns:
        {"found": True, "product": {...}} 或 {"found": False, "error": "..."}
    """
    all_products = {
        "P001": {"id": "P001", "name": "Wireless Bluetooth Headphones", "category": "electronics", "price": 299.00, "rating": 4.5, "stock": 120, "description": "Noise-cancelling wireless headphones with 30h battery life. Bluetooth 5.3, IPX5 water resistant.", "brand": "SoundMax"},
        "P002": {"id": "P002", "name": "iPhone 15 Pro", "category": "electronics", "price": 7999.00, "rating": 4.8, "stock": 50, "description": "A17 Pro chip, 48MP camera, titanium design, USB-C.", "brand": "Apple"},
        "P003": {"id": "P003", "name": "MacBook Pro 14", "category": "electronics", "price": 14999.00, "rating": 4.7, "stock": 30, "description": "M3 Pro chip, 18GB RAM, 512GB SSD, Liquid Retina XDR display.", "brand": "Apple"},
        "P004": {"id": "P004", "name": "Men's Running Shoes", "category": "clothing", "price": 599.00, "rating": 4.3, "stock": 200, "description": "Lightweight mesh upper, responsive cushioning, rubber outsole.", "brand": "Nike"},
        "P005": {"id": "P005", "name": "Coffee Maker", "category": "home", "price": 1299.00, "rating": 4.6, "stock": 80, "description": "Programmable drip coffee maker, 12-cup capacity, built-in grinder.", "brand": "BrewMaster"},
        "P006": {"id": "P006", "name": "Sony WH-1000XM6 Headphones", "category": "electronics", "price": 2499.00, "rating": 4.9, "stock": 15, "description": "Industry-leading noise cancellation, 40h battery, LDAC support.", "brand": "Sony"},
        "P007": {"id": "P007", "name": "Samsung Galaxy S25", "category": "electronics", "price": 6999.00, "rating": 4.6, "stock": 60, "description": "Snapdragon 8 Gen 4, 200MP camera, Dynamic AMOLED 2X.", "brand": "Samsung"},
        "P008": {"id": "P008", "name": "Levi's 501 Jeans", "category": "clothing", "price": 499.00, "rating": 4.4, "stock": 300, "description": "Original fit, non-stretch denim, straight leg.", "brand": "Levi's"},
        "P009": {"id": "P009", "name": "Dyson V15 Vacuum", "category": "home", "price": 4599.00, "rating": 4.8, "stock": 25, "description": "Cordless stick vacuum, laser dust detection, LCD screen.", "brand": "Dyson"},
        "P010": {"id": "P010", "name": "Air Fryer XL", "category": "home", "price": 899.00, "rating": 4.2, "stock": 100, "description": "7L capacity, 12 cooking presets, rapid air circulation.", "brand": "KitchenPro"},
    }

    product = all_products.get(product_id)
    if product:
        return {"found": True, "product": product}
    return {"found": False, "error": f"商品 '{product_id}' 不存在。请检查商品 ID（有效范围: P001-P010）。"}


def add_to_cart(product_id: str, quantity: int = 1) -> dict:
    """添加商品到购物车。

    检查商品是否存在以及库存是否充足。

    Args:
        product_id: 商品 ID（如 P001）。
        quantity: 添加数量（默认 1）。

    Returns:
        成功: {"success": True, "message": "...", "item_total": ...}
        失败: {"success": False, "error": "..."}
    """
    # 简化版商品库存信息
    all_products = {
        "P001": {"name": "Wireless Bluetooth Headphones", "price": 299.00, "stock": 120},
        "P002": {"name": "iPhone 15 Pro", "price": 7999.00, "stock": 50},
        "P003": {"name": "MacBook Pro 14", "price": 14999.00, "stock": 30},
        "P004": {"name": "Men's Running Shoes", "price": 599.00, "stock": 200},
        "P005": {"name": "Coffee Maker", "price": 1299.00, "stock": 80},
        "P006": {"name": "Sony WH-1000XM6 Headphones", "price": 2499.00, "stock": 15},
        "P007": {"name": "Samsung Galaxy S25", "price": 6999.00, "stock": 60},
        "P008": {"name": "Levi's 501 Jeans", "price": 499.00, "stock": 300},
        "P009": {"name": "Dyson V15 Vacuum", "price": 4599.00, "stock": 25},
        "P010": {"name": "Air Fryer XL", "price": 899.00, "stock": 100},
    }

    product = all_products.get(product_id)
    if not product:
        return {"success": False, "error": f"商品 '{product_id}' 不存在。请检查商品 ID。"}

    if quantity > product["stock"]:
        return {"success": False, "error": f"库存不足。{product['name']} 仅有 {product['stock']} 件库存。"}

    return {
        "success": True,
        "message": f"已将 {quantity}x {product['name']} 添加到购物车。",
        "item_total": round(product["price"] * quantity, 2),
    }


def check_order_status(order_id: str) -> dict:
    """查询订单状态。

    支持三种状态：shipped（已发货）、processing（处理中）、delivered（已交付）。

    Args:
        order_id: 订单 ID（如 ORD-12345）。

    Returns:
        成功: {"found": True, "order_id": ..., "status": ..., ...}
        失败: {"found": False, "error": "..."}
    """
    valid_orders = {
        "ORD-12345": {"status": "shipped", "date": "2026-07-25", "items": [{"name": "iPhone 15 Pro", "qty": 1, "price": 7999.00}], "total": 7999.00, "estimated_delivery": "2026-08-02"},
        "ORD-12346": {"status": "processing", "date": "2026-07-29", "items": [{"name": "Coffee Maker", "qty": 1, "price": 1299.00}, {"name": "Air Fryer XL", "qty": 1, "price": 899.00}], "total": 2198.00, "estimated_delivery": "2026-08-05"},
        "ORD-12347": {"status": "delivered", "date": "2026-07-20", "items": [{"name": "Men's Running Shoes", "qty": 1, "price": 599.00}], "total": 599.00, "estimated_delivery": "2026-07-26"},
    }

    order = valid_orders.get(order_id)
    if order:
        return {"found": True, "order_id": order_id, **order}
    return {"found": False, "error": f"订单 '{order_id}' 不存在。有效订单: ORD-12345, ORD-12346, ORD-12347。请核实订单 ID。"}


def get_cart() -> dict:
    """查看当前购物车内容。

    Returns:
        {"cart": {"items": [...], "total_items": N, "total_price": ..., "currency": "CNY"}}
    """
    cart = {
        "items": [
            {"product_id": "P002", "name": "iPhone 15 Pro", "quantity": 1, "price": 7999.00, "subtotal": 7999.00},
            {"product_id": "P004", "name": "Men's Running Shoes", "quantity": 2, "price": 599.00, "subtotal": 1198.00},
        ],
        "total_items": 3,
        "total_price": 9197.00,
        "currency": "CNY",
    }
    return {"cart": cart}


def apply_coupon(code: str) -> dict:
    """应用优惠券折扣。

    支持三种优惠券：
      - SAVE50: 满 ¥200 减 ¥50
      - VIP10: VIP 会员 9 折
      - NEWUSER: 新用户满 ¥100 减 ¥30

    Args:
        code: 优惠券代码（不区分大小写）。

    Returns:
        有效: {"valid": True, "code": ..., "discount": ..., "type": ..., ...}
        无效: {"valid": False, "error": "..."}
    """
    valid_coupons = {
        "SAVE50": {"discount": 50.00, "type": "fixed", "min_purchase": 200.00, "description": "满 ¥200 减 ¥50"},
        "VIP10": {"discount": 0.10, "type": "percent", "min_purchase": 0, "description": "VIP 会员享 9 折"},
        "NEWUSER": {"discount": 30.00, "type": "fixed", "min_purchase": 100.00, "description": "新用户满 ¥100 减 ¥30"},
    }

    coupon = valid_coupons.get(code.upper())
    if coupon:
        return {"valid": True, "code": code.upper(), **coupon}
    return {"valid": False, "error": f"优惠券 '{code}' 无效或已过期。有效优惠券: SAVE50, VIP10, NEWUSER。"}
