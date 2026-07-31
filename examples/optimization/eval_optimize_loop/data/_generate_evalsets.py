#!/usr/bin/env python3
"""生成评测 evalset JSON 文件。

trace/ 子目录: 三件套 trace 数据（demo 模式回放用），每条 case 含 actual_conversation。
live/  子目录: 两件套非 trace 数据（real 模式现调 LLM 用），无 actual_conversation。

每个 case 包含 eval_id、conversation（期望行为）、session_input，scenario 类型
由 eval_id 后缀推导（_optimizable/_ineffective/_working/_regression）。
"""

from __future__ import annotations

import json
import copy
import os

# ============================================================
# 辅助函数
# ============================================================


def _expected_turn_body(user_text: str, tool_name: str, tool_args: dict,
                        expected_response: str, tool_id: str) -> dict:
    """trace 与 live 共享的期望轮次体：user_content + intermediate_data + final_response."""
    return {
        "user_content": {"parts": [{"text": user_text}], "role": "user"},
        "intermediate_data": {
            "tool_uses": [{"id": tool_id, "name": tool_name, "args": tool_args}]
        },
        "final_response": {"parts": [{"text": expected_response}], "role": "model"},
    }


def _with_invocation_id(turn: dict, invocation_id: str) -> dict:
    """trace 专用：在 intermediate_data 与 final_response 之间插入 invocation_id."""
    return {
        "user_content": turn["user_content"],
        "intermediate_data": turn["intermediate_data"],
        "invocation_id": invocation_id,
        "final_response": turn["final_response"],
    }


def _make_turn(inv_id: str, user_text: str, actual_response: str, expected_response: str,
               tool_name: str, tool_args: dict) -> tuple[dict, dict]:
    """创建一个评测轮次，返回 (实际轮次, 期望轮次)。"""
    base = {
        "user_content": {"parts": [{"text": user_text}], "role": "user"},
        "intermediate_data": {
            "tool_uses": [
                {"id": inv_id, "name": tool_name, "args": tool_args}
            ]
        }
    }
    actual = copy.deepcopy(base)
    actual["invocation_id"] = f"{inv_id}-act"
    actual["final_response"] = {"parts": [{"text": actual_response}], "role": "model"}

    expected_body = _expected_turn_body(user_text, tool_name, tool_args, expected_response, inv_id)
    expected = _with_invocation_id(expected_body, f"{inv_id}-exp")

    return actual, expected


def _make_case(eval_id: str, actual_turns: list[dict], expected_turns: list[dict]) -> dict:
    """创建一个评测 case。"""
    return {
        "eval_id": eval_id,
        "eval_mode": "trace",
        "actual_conversation": actual_turns,
        "conversation": expected_turns,
        "session_input": {"app_name": "shopping_assistant", "user_id": "user", "state": {}}
    }


# ============================================================
# Case 定义：每个 case 包含 (eval_id, 用户问题, 工具名, 工具参数,
#   实际回复（差）, 期望回复（好）)
#
# 场景类型由 eval_id 后缀推导（_optimizable / _ineffective / _working / _regression），
# 由后续任务负责。
# ============================================================

TRAIN_CASES = [
    # ---- optimizable: 回复过短，可通过 prompt 要求详细输出修复 ----
    ("train_001_optimizable", "Search for wireless headphones.", "search_products",
     {"query": "wireless headphones"},
     "Found 2 results.",
     "Found 2 wireless headphones: Sony WH-1000XM6 at ¥2,499 (rating 4.9, 15 in stock) and Wireless Bluetooth Headphones at ¥299 (rating 4.5, 120 in stock)."),

    # ---- ineffective: agent 忽略工具返回，幻觉出不存在的订单状态 ----
    ("train_002_ineffective", "Check my order ORD-99999.", "check_order_status",
     {"order_id": "ORD-99999"},
     "Your order ORD-99999 is on the way and will arrive tomorrow.",
     "Order ORD-99999 not found. Please verify the order ID. Valid orders include ORD-12345, ORD-12346, ORD-12347."),

    # ---- working: 正常添加购物车 ----
    ("train_003_working", "Add 2 iPhone 15 Pro to my cart.", "add_to_cart",
     {"product_id": "P002", "quantity": 2},
     "Added 2x iPhone 15 Pro to your cart, totaling ¥15,998.00.",
     "Added 2x iPhone 15 Pro to your cart, totaling ¥15,998.00."),

    # ---- optimizable: 获取商品详情回复过短 ----
    ("train_004_optimizable", "Tell me about the MacBook Pro 14.", "get_product_details",
     {"product_id": "P003"},
     "It's a laptop.",
     "MacBook Pro 14 (P003) by Apple: M3 Pro chip, 18GB RAM, 512GB SSD, Liquid Retina XDR display. Price: ¥14,999, rating 4.7/5, 30 units in stock."),

    # ---- ineffective: 无效优惠券，agent 幻觉成功 ----
    ("train_005_ineffective", "Apply coupon code INVALID99.", "apply_coupon",
     {"code": "INVALID99"},
     "Coupon INVALID99 applied! You saved ¥99.",
     "Coupon INVALID99 is invalid or expired. Valid coupons: SAVE50 (¥50 off orders over ¥200), VIP10 (10% off), NEWUSER (¥30 off, min ¥100)."),

    # ---- working: 查询存在且已发货的订单 ----
    ("train_006_working", "Check the status of order ORD-12345.", "check_order_status",
     {"order_id": "ORD-12345"},
     "Order ORD-12345 has been shipped on 2026-07-25 and is estimated to arrive by 2026-08-02. Total: ¥7,999.",
     "Order ORD-12345 has been shipped on 2026-07-25 and is estimated to arrive by 2026-08-02. Total: ¥7,999."),

    # ---- optimizable: 搜索跑鞋回复过短 ----
    ("train_007_optimizable", "Search for running shoes.", "search_products",
     {"query": "running shoes"},
     "Found 1 result.",
     "Found Men's Running Shoes (P004) by Nike at ¥599, rating 4.3/5, 200 units in stock. Lightweight mesh upper with responsive cushioning."),

    # ---- optimizable: 搜索咖啡机回复过短 ----
    ("train_008_optimizable", "Find coffee makers.", "search_products",
     {"query": "coffee maker"},
     "1 product found.",
     "Found Coffee Maker (P005) by BrewMaster at ¥1,299, rating 4.6/5, 80 units in stock. Programmable drip coffee maker with 12-cup capacity and built-in grinder."),

    # ---- ineffective: 添加不存在的商品，agent 编造成功 ----
    ("train_009_ineffective", "Add product P999 to cart.", "add_to_cart",
     {"product_id": "P999", "quantity": 1},
     "Added product P999 to your cart successfully.",
     "Product P999 does not exist. Please check the product ID. Valid products include P001 through P010."),

    # ---- working: 查看购物车 ----
    ("train_010_working", "What's in my shopping cart?", "get_cart",
     {},
     "Your cart has 3 items: iPhone 15 Pro x1 (¥7,999), Men's Running Shoes x2 (¥1,198). Total: ¥9,197 CNY.",
     "Your cart has 3 items: iPhone 15 Pro x1 (¥7,999), Men's Running Shoes x2 (¥1,198). Total: ¥9,197 CNY."),

    # ---- optimizable: 搜索吸尘器回复过短 ----
    ("train_011_optimizable", "Search for vacuum cleaners.", "search_products",
     {"query": "vacuum"},
     "1 result.",
     "Found Dyson V15 Vacuum (P009) by Dyson at ¥4,599, rating 4.8/5, 25 units in stock. Cordless stick vacuum with laser dust detection and LCD screen."),

    # ---- ineffective: 查询另一个不存在的订单，agent 幻觉 ----
    ("train_012_ineffective", "Where is my order ORD-88888?", "check_order_status",
     {"order_id": "ORD-88888"},
     "Your order ORD-88888 is being processed and will ship soon.",
     "Order ORD-88888 not found. Please verify the order ID. Valid orders include ORD-12345, ORD-12346, ORD-12347."),

    # ---- working: 应用有效优惠券 SAVE50 ----
    ("train_013_working", "Apply coupon SAVE50 to my order.", "apply_coupon",
     {"code": "SAVE50"},
     "Coupon SAVE50 applied! You save ¥50.00 on orders over ¥200.",
     "Coupon SAVE50 applied! You save ¥50.00 on orders over ¥200."),

    # ---- optimizable: 获取三星手机详情过短 ----
    ("train_014_optimizable", "Tell me about the Samsung Galaxy S25.", "get_product_details",
     {"product_id": "P007"},
     "It's a phone from Samsung.",
     "Samsung Galaxy S25 (P007) by Samsung: Snapdragon 8 Gen 4, 200MP camera, Dynamic AMOLED 2X display. Price: ¥6,999, rating 4.6/5, 60 units in stock."),

    # ---- optimizable: 获取索尼耳机详情过短 ----
    ("train_015_optimizable", "Details of Sony WH-1000XM6 headphones.", "get_product_details",
     {"product_id": "P006"},
     "Those are headphones.",
     "Sony WH-1000XM6 (P006) by Sony: Industry-leading noise cancellation, 40h battery life, LDAC support. Price: ¥2,499, rating 4.9/5, 15 units in stock."),

    # ---- ineffective: 超量添加，agent 忽略库存限制 ----
    ("train_016_ineffective", "Add 200 MacBook Pro 14 to cart.", "add_to_cart",
     {"product_id": "P003", "quantity": 200},
     "Added 200 MacBook Pro 14 to your cart.",
     "Insufficient stock. Only 30 units of MacBook Pro 14 available. Cannot add 200 units to cart."),

    # ---- working: 查询已交付订单 ----
    ("train_017_working", "Check my order ORD-12347 status.", "check_order_status",
     {"order_id": "ORD-12347"},
     "Order ORD-12347 was delivered on 2026-07-26. Contains Men's Running Shoes x1, total ¥599.",
     "Order ORD-12347 was delivered on 2026-07-26. Contains Men's Running Shoes x1, total ¥599."),

    # ---- optimizable: 获取戴森吸尘器详情过短 ----
    ("train_018_optimizable", "Tell me about Dyson V15.", "get_product_details",
     {"product_id": "P009"},
     "A vacuum cleaner.",
     "Dyson V15 Vacuum (P009) by Dyson: Cordless stick vacuum with laser dust detection and LCD screen. Price: ¥4,599, rating 4.8/5, 25 units in stock."),

    # ---- ineffective: 应用不存在的优惠券（变体），agent 幻觉 ----
    ("train_019_ineffective", "Use promo code SUPER50.", "apply_coupon",
     {"code": "SUPER50"},
     "Promo code SUPER50 accepted! 50% off applied.",
     "Coupon SUPER50 is invalid or expired. Valid coupons: SAVE50 (¥50 off orders over ¥200), VIP10 (10% off), NEWUSER (¥30 off, min ¥100)."),

    # ---- optimizable: 搜索牛仔裤回复过短 ----
    ("train_020_optimizable", "Search for jeans.", "search_products",
     {"query": "jeans"},
     "1 pair found.",
     "Found Levi's 501 Jeans (P008) by Levi's at ¥499, rating 4.4/5, 300 units in stock. Original fit, non-stretch denim, straight leg."),
]

# 验证集 cases：与训练集类似但使用不同的 eval_id 和查询
VAL_CASES = [
    # ---- optimizable: 获取商品详情回复过短，可通过 prompt 修复 ----
    ("val_001_optimizable", "Tell me about the MacBook Pro 14.", "get_product_details",
     {"product_id": "P003"},
     "It's a laptop.",
     "MacBook Pro 14 (P003) by Apple: M3 Pro chip, 18GB RAM, 512GB SSD, Liquid Retina XDR display. Price: ¥14,999, rating 4.7/5, 30 units in stock."),

    # ---- ineffective: 无效优惠券幻觉，prompt 无法修复 ----
    ("val_002_ineffective", "Apply coupon code INVALID99.", "apply_coupon",
     {"code": "INVALID99"},
     "Coupon INVALID99 applied! You saved ¥99.",
     "Coupon INVALID99 is invalid or expired. Valid coupons: SAVE50 (¥50 off orders over ¥200), VIP10 (10% off), NEWUSER (¥30 off, min ¥100)."),

    # ---- regression risk: 当前 PASSED，优化后可能退化 ----
    ("val_003_regression", "What's in my shopping cart?", "get_cart",
     {},
     "Your cart has 3 items: iPhone 15 Pro x1 (¥7,999), Men's Running Shoes x2 (¥1,198). Total: ¥9,197 CNY.",
     "Your cart has 3 items: iPhone 15 Pro x1 (¥7,999), Men's Running Shoes x2 (¥1,198). Total: ¥9,197 CNY."),

    # ---- optimizable: 搜索耳机回复过短 ----
    ("val_004_optimizable", "Find wireless headphones for me.", "search_products",
     {"query": "wireless headphones"},
     "Here are the results.",
     "Found 2 wireless headphones: Sony WH-1000XM6 at ¥2,499 (rating 4.9, 15 in stock) and Wireless Bluetooth Headphones at ¥299 (rating 4.5, 120 in stock)."),

    # ---- regression risk: 当前 PASSED，优化后可能出错 ----
    ("val_005_regression", "Where is my order ORD-12346?", "check_order_status",
     {"order_id": "ORD-12346"},
     "Order ORD-12346 is processing. Items: Coffee Maker x1, Air Fryer XL x1. Total: ¥2,198. Estimated delivery: 2026-08-05.",
     "Order ORD-12346 is processing. Items: Coffee Maker x1, Air Fryer XL x1. Total: ¥2,198. Estimated delivery: 2026-08-05."),

    # ---- optimizable: 获取 iPhone 详情过短 ----
    ("val_006_optimizable", "Show me details of iPhone 15 Pro.", "get_product_details",
     {"product_id": "P002"},
     "iPhone.",
     "iPhone 15 Pro (P002) by Apple: A17 Pro chip, 48MP camera, titanium design, USB-C. Price: ¥7,999, rating 4.8/5, 50 units in stock."),

    # ---- ineffective: 查询不存在的订单，agent 幻觉 ----
    ("val_007_ineffective", "Track my order ORD-77777.", "check_order_status",
     {"order_id": "ORD-77777"},
     "Your order ORD-77777 is out for delivery today.",
     "Order ORD-77777 not found. Please verify the order ID. Valid orders include ORD-12345, ORD-12346, ORD-12347."),

    # ---- optimizable: 搜索空气炸锅回复过短 ----
    ("val_008_optimizable", "Find air fryers.", "search_products",
     {"query": "air fryer"},
     "Found it.",
     "Found Air Fryer XL (P010) by KitchenPro at ¥899, rating 4.2/5, 100 units in stock. 7L capacity with 12 cooking presets and rapid air circulation."),

    # ---- regression risk: 当前 PASSED，优化后可能画蛇添足 ----
    ("val_009_regression", "Add a pair of running shoes to my cart.", "add_to_cart",
     {"product_id": "P004", "quantity": 1},
     "Added 1x Men's Running Shoes to your cart, totaling ¥599.00.",
     "Added 1x Men's Running Shoes to your cart, totaling ¥599.00."),

    # ---- optimizable: 获取 Levi's 牛仔裤详情过短 ----
    ("val_010_optimizable", "Tell me about Levi's 501 Jeans.", "get_product_details",
     {"product_id": "P008"},
     "Jeans by Levi's.",
     "Levi's 501 Jeans (P008) by Levi's: Original fit, non-stretch denim, straight leg. Price: ¥499, rating 4.4/5, 300 units in stock."),

    # ---- ineffective: 无效优惠券（变体），agent 幻觉 ----
    ("val_011_ineffective", "Redeem coupon FREEDELIVERY.", "apply_coupon",
     {"code": "FREEDELIVERY"},
     "FREEDELIVERY coupon activated! Free shipping on your order.",
     "Coupon FREEDELIVERY is invalid or expired. Valid coupons: SAVE50 (¥50 off orders over ¥200), VIP10 (10% off), NEWUSER (¥30 off, min ¥100)."),

    # ---- optimizable: 搜索电子产品回复过短 ----
    ("val_012_optimizable", "Show me electronics under ¥1000.", "search_products",
     {"query": "electronics", "category": "electronics"},
     "No results.",
     "Found Wireless Bluetooth Headphones (P001) at ¥299, rating 4.5/5, 120 in stock. Other electronics start from ¥2,499+."),

    # ---- regression risk: 当前 PASSED，优化后委托可能变化 ----
    ("val_013_regression", "Apply VIP10 coupon code.", "apply_coupon",
     {"code": "VIP10"},
     "Coupon VIP10 applied! 10% off for VIP members has been applied to your order.",
     "Coupon VIP10 applied! 10% off for VIP members has been applied to your order."),

    # ---- optimizable: 获取咖啡机详情过短 ----
    ("val_014_optimizable", "Give me details on the Coffee Maker.", "get_product_details",
     {"product_id": "P005"},
     "A coffee machine.",
     "Coffee Maker (P005) by BrewMaster: Programmable drip coffee maker, 12-cup capacity, built-in grinder. Price: ¥1,299, rating 4.6/5, 80 units in stock."),

    # ---- ineffective: 添加不存在商品，agent 编造 ----
    ("val_015_ineffective", "Add product P888 to my cart.", "add_to_cart",
     {"product_id": "P888", "quantity": 1},
     "Added P888 to cart.",
     "Product P888 does not exist. Please check the product ID. Valid products include P001 through P010."),

    # ---- optimizable: 获取索尼耳机详情过短 ----
    ("val_016_optimizable", "What can you tell me about Sony WH-1000XM6?", "get_product_details",
     {"product_id": "P006"},
     "Headphones.",
     "Sony WH-1000XM6 (P006) by Sony: Industry-leading noise cancellation, 40h battery life, LDAC support. Price: ¥2,499, rating 4.9/5, 15 units in stock."),

    # ---- optimizable: 搜索+详情两步操作，回复过短 ----
    ("val_017_optimizable", "I need a new phone. What do you have?", "search_products",
     {"query": "phone"},
     "2 phones available.",
     "Found 2 phones: iPhone 15 Pro (P002) at ¥7,999 (rating 4.8) and Samsung Galaxy S25 (P007) at ¥6,999 (rating 4.6). Both are great options with different features."),

    # ---- ineffective: 查询不存在的订单（变体），agent 幻觉 ----
    ("val_018_ineffective", "Order ORD-00001 status please.", "check_order_status",
     {"order_id": "ORD-00001"},
     "ORD-00001 is pending shipment.",
     "Order ORD-00001 not found. Please verify the order ID. Valid orders include ORD-12345, ORD-12346, ORD-12347."),

    # ---- regression risk: 当前 PASSED，优化后优惠券可能被错误处理 ----
    ("val_019_regression", "Use my NEWUSER coupon.", "apply_coupon",
     {"code": "NEWUSER"},
     "Coupon NEWUSER applied! ¥30 off, valid for new users with minimum ¥100 purchase.",
     "Coupon NEWUSER applied! ¥30 off, valid for new users with minimum ¥100 purchase."),

    # ---- optimizable: 获取 Air Fryer 详情过短 ----
    ("val_020_optimizable", "Tell me about the Air Fryer XL.", "get_product_details",
     {"product_id": "P010"},
     "A kitchen appliance.",
     "Air Fryer XL (P010) by KitchenPro: 7L capacity, 12 cooking presets, rapid air circulation. Price: ¥899, rating 4.2/5, 100 units in stock."),
]


def generate_evalset(eval_set_id: str, name: str, description: str, cases: list) -> dict:
    """根据 case 定义列表生成 evalset JSON 结构。"""
    eval_cases = []
    for case_def in cases:
        eval_id, user_text, tool_name, tool_args, actual_resp, expected_resp = case_def[:6]

        actual_turn, expected_turn = _make_turn(
            inv_id=eval_id.replace("train_", "t").replace("val_", "v"),
            user_text=user_text,
            actual_response=actual_resp,
            expected_response=expected_resp,
            tool_name=tool_name,
            tool_args=tool_args,
        )

        eval_cases.append(_make_case(
            eval_id=eval_id,
            actual_turns=[actual_turn],
            expected_turns=[expected_turn],
        ))

    return {
        "eval_set_id": eval_set_id,
        "name": name,
        "description": description,
        "eval_cases": eval_cases,
    }


def generate_optimized_evalset(val_cases: list) -> dict:
    """生成优化后的验证集 evalset。

    规则：
    - optimizable cases: actual_conversation 的 final_response 改为与 conversation 一致（表示优化成功）
    - ineffective cases: 保持不变（优化无法修复）
    - regression cases: actual_conversation 的 final_response 改为退化版本（表示过拟合）
    """
    # 退化响应映射：eval_id → 退化后的实际响应
    regression_responses = {
        "val_003_regression": "Let me search for items to add to your cart. What would you like to buy?",
        "val_005_regression": "Order ORD-12346 is shipped and arriving soon.",
        "val_009_regression": "Running shoes are great! Would you also like some socks or athletic wear?",
        "val_013_regression": "VIP10 is applied. You now have 10% more loyalty points.",
        "val_019_regression": "NEWUSER applied. But you may want to try SAVE50 for a better discount instead.",
    }

    eval_cases = []
    for case_def in val_cases:
        eval_id, user_text, tool_name, tool_args, base_actual, expected_resp = case_def[:6]
        if "_optimizable" in eval_id:
            scenario = "optimizable_success"
        elif "_ineffective" in eval_id:
            scenario = "optimization_ineffective"
        elif "_regression" in eval_id:
            scenario = "optimization_regression"
        else:
            raise ValueError(f"Unknown scenario suffix in eval_id: {eval_id}")

        actual_turn, expected_turn = _make_turn(
            inv_id=eval_id.replace("val_", "o"),
            user_text=user_text,
            actual_response=base_actual,
            expected_response=expected_resp,
            tool_name=tool_name,
            tool_args=tool_args,
        )

        # 根据场景类型调整实际响应
        if scenario == "optimizable_success":
            # 优化成功：actual 改为 expected（PASS）
            actual_turn["final_response"] = {"parts": [{"text": expected_resp}], "role": "model"}
        elif scenario == "optimization_regression":
            # 过拟合退化：actual 改为退化版本（FAILED）
            actual_turn["final_response"] = {"parts": [{"text": regression_responses[eval_id]}], "role": "model"}
        # ineffective: 保持不变

        eval_cases.append(_make_case(
            eval_id=eval_id,
            actual_turns=[actual_turn],
            expected_turns=[expected_turn],
        ))

    return {
        "eval_set_id": "shopping_assistant_val_optimized",
        "name": "Shopping Assistant Validation Set (Post-Optimization)",
        "description": f"20 validation cases reflecting agent behavior after prompt optimization. Optimizable cases now pass, ineffective cases unchanged, regression cases show overfitting.",
        "eval_cases": eval_cases,
    }


def generate_live_evalset(eval_set_id: str, name: str, description: str, cases: list) -> dict:
    """生成 live 数据集: 无 actual_conversation, 仅期望 + session_input."""
    eval_cases = []
    for case_def in cases:
        eval_id, user_text, tool_name, tool_args, _actual, expected_resp = case_def[:6]
        expected_turn = _expected_turn_body(
            user_text=user_text,
            tool_name=tool_name,
            tool_args=tool_args,
            expected_response=expected_resp,
            tool_id=eval_id.replace("train_", "lt").replace("val_", "lv"),
        )
        eval_cases.append({
            "eval_id": eval_id,
            "eval_mode": "non-trace",
            "conversation": [expected_turn],
            "session_input": {"app_name": "shopping_assistant", "user_id": "user", "state": {}},
        })
    return {
        "eval_set_id": eval_set_id,
        "name": name,
        "description": description,
        "eval_cases": eval_cases,
    }


def main():
    data_dir = os.path.dirname(os.path.abspath(__file__))

    # ---- trace 三件套 ----
    trace_dir = os.path.join(data_dir, "trace")
    os.makedirs(trace_dir, exist_ok=True)

    train = generate_evalset("shopping_assistant_train", "Training Set", "20 training cases.", TRAIN_CASES)
    val_base = generate_evalset("shopping_assistant_val", "Validation Baseline", "20 validation cases.", VAL_CASES)
    val_opt = generate_optimized_evalset(VAL_CASES)

    for name, payload in [("train.evalset.json", train), ("val_baseline.evalset.json", val_base), ("val_optimized.evalset.json", val_opt)]:
        p = os.path.join(trace_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"Generated {p} ({len(payload['eval_cases'])} cases)")

    # ---- live 两件套（不同路径! 否则 AgentOptimizer 抛 ValueError） ----
    live_dir = os.path.join(data_dir, "live")
    os.makedirs(live_dir, exist_ok=True)

    train_live = generate_live_evalset("shopping_assistant_train_live", "Training (live)", "20 training cases for live mode.", TRAIN_CASES)
    val_live = generate_live_evalset("shopping_assistant_val_live", "Validation (live)", "20 validation cases for live mode.", VAL_CASES)

    train_live_path = os.path.join(live_dir, "train.evalset.json")
    val_live_path = os.path.join(live_dir, "val.evalset.json")
    for path, payload in [(train_live_path, train_live), (val_live_path, val_live)]:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"Generated {path} ({len(payload['eval_cases'])} cases)")

    # ---- 删除旧的扁平文件 ----
    for stale in ["train_baseline.evalset.json", "val_baseline.evalset.json", "val_optimized.evalset.json"]:
        stale_path = os.path.join(data_dir, stale)
        if os.path.exists(stale_path):
            os.remove(stale_path)
            print(f"Removed {stale_path}")


if __name__ == "__main__":
    main()
