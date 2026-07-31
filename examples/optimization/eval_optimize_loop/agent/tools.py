"""电商购物助手工具函数。

提供 7 个电商场景的工具函数，供 agent 调用：

  - search_products(query, category)  — 搜索商品，支持按分类过滤
  - get_product_details(product_id)   — 获取商品详细信息
  - add_to_cart(product_id, quantity) — 添加商品到购物车
  - get_cart()                        — 查看购物车内容
  - apply_coupon(code)                — 应用优惠券
  - checkout()                        — 结算购物车，生成订单
  - check_order_status(order_id)      — 查询订单状态

商品、购物车、订单数据通过 CSV 文件共享存储，保证数据一致性：
  - agent/data/products.csv — 商品目录（单一数据源）
  - agent/data/cart.csv     — 购物车（add_to_cart 写入，get_cart 读取）
  - agent/data/orders.csv   — 订单记录（含 3 条历史种子订单，checkout 追加，check_order_status 读取）
"""

import csv
import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# ── 数据文件路径 ──────────────────────────────────────────────
_HERE = Path(__file__).parent
DATA_DIR = _HERE / "data"

PRODUCTS_CSV = DATA_DIR / "products.csv"
CART_CSV = DATA_DIR / "cart.csv"
ORDERS_CSV = DATA_DIR / "orders.csv"

# ── 静态优惠券配置 ────────────────────────────────────────────
_COUPONS = {
    "SAVE50": {"discount": 50.00, "type": "fixed", "min_purchase": 200.00, "description": "满 ¥200 减 ¥50"},
    "VIP10": {"discount": 0.10, "type": "percent", "min_purchase": 0, "description": "VIP 会员享 9 折"},
    "NEWUSER": {"discount": 30.00, "type": "fixed", "min_purchase": 100.00, "description": "新用户满 ¥100 减 ¥30"},
}

# ── 默认商品目录 ──────────────────────────────────────────────
_DEFAULT_PRODUCTS = [
    {"id": "P001", "name": "Wireless Bluetooth Headphones", "category": "electronics", "price": "299.00", "rating": "4.5", "stock": "120", "description": "Noise-cancelling wireless headphones with 30h battery life. Bluetooth 5.3, IPX5 water resistant.", "brand": "SoundMax"},
    {"id": "P002", "name": "iPhone 15 Pro", "category": "electronics", "price": "7999.00", "rating": "4.8", "stock": "50", "description": "A17 Pro chip, 48MP camera, titanium design, USB-C.", "brand": "Apple"},
    {"id": "P003", "name": "MacBook Pro 14", "category": "electronics", "price": "14999.00", "rating": "4.7", "stock": "30", "description": "M3 Pro chip, 18GB RAM, 512GB SSD, Liquid Retina XDR display.", "brand": "Apple"},
    {"id": "P004", "name": "Men's Running Shoes", "category": "clothing", "price": "599.00", "rating": "4.3", "stock": "200", "description": "Lightweight mesh upper, responsive cushioning, rubber outsole.", "brand": "Nike"},
    {"id": "P005", "name": "Coffee Maker", "category": "home", "price": "1299.00", "rating": "4.6", "stock": "80", "description": "Programmable drip coffee maker, 12-cup capacity, built-in grinder.", "brand": "BrewMaster"},
    {"id": "P006", "name": "Sony WH-1000XM6 Headphones", "category": "electronics", "price": "2499.00", "rating": "4.9", "stock": "15", "description": "Industry-leading noise cancellation, 40h battery, LDAC support.", "brand": "Sony"},
    {"id": "P007", "name": "Samsung Galaxy S25", "category": "electronics", "price": "6999.00", "rating": "4.6", "stock": "60", "description": "Snapdragon 8 Gen 4, 200MP camera, Dynamic AMOLED 2X.", "brand": "Samsung"},
    {"id": "P008", "name": "Levi's 501 Jeans", "category": "clothing", "price": "499.00", "rating": "4.4", "stock": "300", "description": "Original fit, non-stretch denim, straight leg.", "brand": "Levi's"},
    {"id": "P009", "name": "Dyson V15 Vacuum", "category": "home", "price": "4599.00", "rating": "4.8", "stock": "25", "description": "Cordless stick vacuum, laser dust detection, LCD screen.", "brand": "Dyson"},
    {"id": "P010", "name": "Air Fryer XL", "category": "home", "price": "899.00", "rating": "4.2", "stock": "100", "description": "7L capacity, 12 cooking presets, rapid air circulation.", "brand": "KitchenPro"},
]

# 历史订单种子数据（评测用例引用了这 3 个订单，必须存在）
_DEFAULT_ORDERS = [
    {"order_id": "ORD-12345", "status": "shipped", "date": "2026-07-25",
     "items": '[{"name": "iPhone 15 Pro", "qty": 1, "price": 7999.0}]',
     "total": "7999.00", "estimated_delivery": "2026-08-02"},
    {"order_id": "ORD-12346", "status": "processing", "date": "2026-07-29",
     "items": '[{"name": "Coffee Maker", "qty": 1, "price": 1299.0}, {"name": "Air Fryer XL", "qty": 1, "price": 899.0}]',
     "total": "2198.00", "estimated_delivery": "2026-08-05"},
    {"order_id": "ORD-12347", "status": "delivered", "date": "2026-07-20",
     "items": '[{"name": "Men\'s Running Shoes", "qty": 1, "price": 599.0}]',
     "total": "599.00", "estimated_delivery": "2026-07-26"},
]

_PRODUCT_FIELDS = list(_DEFAULT_PRODUCTS[0].keys())
_CART_FIELDS = ["product_id", "name", "quantity", "price", "subtotal"]
_ORDER_FIELDS = ["order_id", "status", "date", "items", "total", "estimated_delivery"]


# ── CSV 读写辅助 ──────────────────────────────────────────────

def _read_csv(filepath: Path) -> list[dict]:
    """读取 CSV 文件，返回 dict 列表。文件不存在时返回空列表。"""
    if not filepath.exists():
        return []
    with open(filepath, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _write_csv(filepath: Path, fieldnames: list[str], rows: list[dict]) -> None:
    """将 dict 列表写入 CSV 文件。"""
    with open(filepath, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _init_data() -> None:
    """初始化数据目录和默认 CSV 文件（仅首次运行时创建）。

    orders.csv 以历史订单种子初始化——评测用例依赖 ORD-12345/46/47。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not PRODUCTS_CSV.exists():
        _write_csv(PRODUCTS_CSV, _PRODUCT_FIELDS, _DEFAULT_PRODUCTS)
    if not CART_CSV.exists():
        _write_csv(CART_CSV, _CART_FIELDS, [])
    if not ORDERS_CSV.exists():
        _write_csv(ORDERS_CSV, _ORDER_FIELDS, _DEFAULT_ORDERS)


def reset_data() -> None:
    """重置所有数据到初始状态（用于评测/测试间清理）。"""
    _write_csv(PRODUCTS_CSV, _PRODUCT_FIELDS, _DEFAULT_PRODUCTS)
    _write_csv(CART_CSV, _CART_FIELDS, [])
    _write_csv(ORDERS_CSV, _ORDER_FIELDS, _DEFAULT_ORDERS)


# 模块加载时初始化数据
_init_data()


# ── 工具函数 ──────────────────────────────────────────────────

def search_products(query: str, category: Optional[str] = None) -> dict:
    """搜索商品：按关键词和可选分类过滤。

    Args:
        query: 商品名称或描述的关键词（不区分大小写）。
        category: 可选的分类过滤（electronics, clothing, home）。

    Returns:
        {"query": ..., "category": ..., "total": N, "products": [...]}
    """
    products = _read_csv(PRODUCTS_CSV)
    query_lower = query.lower()
    results = []
    for p in products:
        if query_lower in p["name"].lower():
            if category is None or p["category"] == category:
                results.append({
                    "id": p["id"],
                    "name": p["name"],
                    "category": p["category"],
                    "price": float(p["price"]),
                    "rating": float(p["rating"]),
                    "stock": int(p["stock"]),
                })
    return {"query": query, "category": category, "total": len(results), "products": results}


def get_product_details(product_id: str) -> dict:
    """根据商品 ID 获取商品详细信息。

    Args:
        product_id: 商品 ID（如 P001）。

    Returns:
        {"found": True, "product": {...}} 或 {"found": False, "error": "..."}
    """
    products = _read_csv(PRODUCTS_CSV)
    for p in products:
        if p["id"] == product_id:
            return {"found": True, "product": {
                "id": p["id"],
                "name": p["name"],
                "category": p["category"],
                "price": float(p["price"]),
                "rating": float(p["rating"]),
                "stock": int(p["stock"]),
                "description": p["description"],
                "brand": p["brand"],
            }}
    return {"found": False, "error": f"商品 '{product_id}' 不存在。请检查商品 ID（有效范围: P001-P010）。"}


def add_to_cart(product_id: str, quantity: int = 1) -> dict:
    """添加商品到购物车，检查商品是否存在及库存是否充足。

    同一商品重复添加时累加数量，不会创建重复条目。

    Args:
        product_id: 商品 ID（如 P001）。
        quantity: 添加数量（默认 1）。

    Returns:
        成功: {"success": True, "message": "...", "item_total": ...}
        失败: {"success": False, "error": "..."}
    """
    products = _read_csv(PRODUCTS_CSV)
    product = None
    for p in products:
        if p["id"] == product_id:
            product = p
            break

    if product is None:
        return {"success": False, "error": f"商品 '{product_id}' 不存在。请检查商品 ID。"}

    stock = int(product["stock"])
    if quantity > stock:
        return {"success": False, "error": f"库存不足。{product['name']} 仅有 {stock} 件库存。"}

    price = float(product["price"])
    cart = _read_csv(CART_CSV)

    # 购物车已有该商品则累加数量
    for item in cart:
        if item["product_id"] == product_id:
            new_qty = int(item["quantity"]) + quantity
            if new_qty > stock:
                return {"success": False, "error": f"库存不足。购物车已有 {item['quantity']} 件，再加 {quantity} 件将超出 {product['name']} 的 {stock} 件库存。"}
            item["quantity"] = str(new_qty)
            item["subtotal"] = str(round(price * new_qty, 2))
            _write_csv(CART_CSV, _CART_FIELDS, cart)
            return {
                "success": True,
                "message": f"已将 {quantity}x {product['name']} 添加到购物车（购物车现有 {new_qty} 件）。",
                "item_total": round(price * quantity, 2),
            }

    # 新商品加入购物车
    cart.append({
        "product_id": product_id,
        "name": product["name"],
        "quantity": str(quantity),
        "price": str(price),
        "subtotal": str(round(price * quantity, 2)),
    })
    _write_csv(CART_CSV, _CART_FIELDS, cart)

    return {
        "success": True,
        "message": f"已将 {quantity}x {product['name']} 添加到购物车。",
        "item_total": round(price * quantity, 2),
    }


def get_cart() -> dict:
    """查看当前购物车内容。

    Returns:
        {"cart": {"items": [...], "total_items": N, "total_price": ..., "currency": "CNY"}}
    """
    cart = _read_csv(CART_CSV)
    items = []
    total_items = 0
    total_price = 0.0
    for item in cart:
        qty = int(item["quantity"])
        subtotal = float(item["subtotal"])
        items.append({
            "product_id": item["product_id"],
            "name": item["name"],
            "quantity": qty,
            "price": float(item["price"]),
            "subtotal": subtotal,
        })
        total_items += qty
        total_price += subtotal

    return {
        "cart": {
            "items": items,
            "total_items": total_items,
            "total_price": round(total_price, 2),
            "currency": "CNY",
        }
    }


def checkout() -> dict:
    """结算购物车，生成订单并清空购物车。

    将购物车中所有商品转为订单记录，生成唯一订单号，
    订单状态初始为 "processing"，预计 7 天后送达。
    结算成功后购物车自动清空。

    Returns:
        成功: {"success": True, "order_id": "ORD-...", "status": "processing", ...}
        失败: {"success": False, "error": "购物车为空，无法结算。"}
    """
    cart = _read_csv(CART_CSV)
    if not cart:
        return {"success": False, "error": "购物车为空，无法结算。"}

    items = []
    total = 0.0
    for item in cart:
        qty = int(item["quantity"])
        price = float(item["price"])
        subtotal = float(item["subtotal"])
        items.append({"name": item["name"], "qty": qty, "price": price})
        total += subtotal

    total = round(total, 2)
    order_id = f"ORD-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:4].upper()}"
    date = datetime.now().strftime("%Y-%m-%d")
    estimated_delivery = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    orders = _read_csv(ORDERS_CSV)
    orders.append({
        "order_id": order_id,
        "status": "processing",
        "date": date,
        "items": json.dumps(items, ensure_ascii=False),
        "total": str(total),
        "estimated_delivery": estimated_delivery,
    })
    _write_csv(ORDERS_CSV, _ORDER_FIELDS, orders)

    # 结算成功，清空购物车
    _write_csv(CART_CSV, _CART_FIELDS, [])

    return {
        "success": True,
        "order_id": order_id,
        "status": "processing",
        "date": date,
        "items": items,
        "total": total,
        "estimated_delivery": estimated_delivery,
        "message": f"订单 {order_id} 已生成，总金额 ¥{total}，预计 {estimated_delivery} 送达。",
    }


def check_order_status(order_id: str) -> dict:
    """利用订单号查询订单状态。

    支持三种状态：shipped（已发货）、processing（处理中）、delivered（已交付）。

    Args:
        order_id: 订单 ID（如 ORD-20260731-A1B2），注意要用大写。

    Returns:
        成功: {"found": True, "order_id": ..., "status": ..., ...}
        失败: {"found": False, "error": "..."}
    """
    orders = _read_csv(ORDERS_CSV)
    for order in orders:
        if order["order_id"] == order_id:
            return {
                "found": True,
                "order_id": order_id,
                "status": order["status"],
                "date": order["date"],
                "items": json.loads(order["items"]),
                "total": float(order["total"]),
                "estimated_delivery": order["estimated_delivery"],
            }
    return {"found": False, "error": f"订单 '{order_id}' 不存在。请核实订单 ID。"}


def apply_coupon(code: str) -> dict:
    """应用优惠券折扣。

    支持三种优惠券编号：
      - SAVE50: 满 ¥200 减 ¥50
      - VIP10: VIP 会员 9 折
      - NEWUSER: 新用户满 ¥100 减 ¥30

    Args:
        code: 优惠券编号（区分大小写，全都用大写）。

    Returns:
        有效: {"valid": True, "code": ..., "discount": ..., "type": ..., ...}
        无效: {"valid": False, "error": "..."}
    """
    coupon = _COUPONS.get(code.upper())
    if coupon:
        return {"valid": True, "code": code.upper(), **coupon}
    return {"valid": False, "error": f"优惠券 '{code}' 无效或已过期。有效优惠券: SAVE50, VIP10, NEWUSER。"}
