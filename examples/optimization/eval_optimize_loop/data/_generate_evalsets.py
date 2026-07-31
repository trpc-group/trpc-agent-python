#!/usr/bin/env python3
"""生成评测 evalset JSON 文件。

trace/ 子目录: 三件套 trace 数据（demo 模式回放用），每条 case 含 actual_conversation。
live/  子目录: 两件套非 trace 数据（real 模式现调 LLM 用），无 actual_conversation。

每个 case 包含 eval_id、conversation（期望行为）、session_input，scenario 类型
由 eval_id 后缀推导（_optimizable/_ineffective/_working/_regression）。

所有用户提问与模型回复均为中文。
"""

from __future__ import annotations

import json
import copy
import os
import re

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
    ("train_001_optimizable", "帮我搜索无线耳机。", "search_products",
     {"query": "wireless headphones"},
     "找到 2 个结果。",
     "找到 2 款无线耳机：Sony WH-1000XM6 售价 ¥2,499（评分 4.9，库存 15 件），Wireless Bluetooth Headphones 售价 ¥299（评分 4.5，库存 120 件）。"),

    # ---- ineffective: agent 忽略工具返回，幻觉出不存在的订单状态 ----
    ("train_002_ineffective", "查询我的订单 ORD-99999。", "check_order_status",
     {"order_id": "ORD-99999"},
     "您的订单 ORD-99999 正在运输途中，明天送达。",
     "订单 ORD-99999 不存在，请核实订单 ID。有效订单：ORD-12345、ORD-12346、ORD-12347。"),

    # ---- working: 正常添加购物车 ----
    ("train_003_working", "往我的购物车里加 2 个 iPhone 15 Pro。", "add_to_cart",
     {"product_id": "P002", "quantity": 2},
     "已将 2 个 iPhone 15 Pro 加入购物车，合计 ¥15,998.00。",
     "已将 2 个 iPhone 15 Pro 加入购物车，合计 ¥15,998.00。"),

    # ---- optimizable: 获取商品详情回复过短 ----
    ("train_004_optimizable", "介绍一下 MacBook Pro 14。", "get_product_details",
     {"product_id": "P003"},
     "它是一台笔记本电脑。",
     "MacBook Pro 14（P003）苹果出品：M3 Pro 芯片，18GB 内存，512GB 固态硬盘，Liquid Retina XDR 显示屏。售价 ¥14,999，评分 4.7/5，库存 30 件。"),

    # ---- ineffective: 无效优惠券，agent 幻觉成功 ----
    ("train_005_ineffective", "帮我用优惠券 INVALID99。", "apply_coupon",
     {"code": "INVALID99"},
     "优惠券 INVALID99 已使用！为您节省 ¥99。",
     "优惠券 INVALID99 无效或已过期。有效优惠券：SAVE50（满 ¥200 减 ¥50）、VIP10（9 折）、NEWUSER（满 ¥100 减 ¥30）。"),

    # ---- working: 查询存在且已发货的订单 ----
    ("train_006_working", "查询订单 ORD-12345 的状态。", "check_order_status",
     {"order_id": "ORD-12345"},
     "订单 ORD-12345 已于 2026-07-25 发货，预计 2026-08-02 送达。总金额 ¥7,999。",
     "订单 ORD-12345 已于 2026-07-25 发货，预计 2026-08-02 送达。总金额 ¥7,999。"),

    # ---- optimizable: 搜索跑鞋回复过短 ----
    ("train_007_optimizable", "搜索跑步鞋。", "search_products",
     {"query": "running shoes"},
     "找到 1 个结果。",
     "找到 Men's Running Shoes（P004）耐克出品，售价 ¥599，评分 4.3/5，库存 200 件。轻量网面鞋身，缓震回弹良好。"),

    # ---- optimizable: 搜索咖啡机回复过短 ----
    ("train_008_optimizable", "找找咖啡机。", "search_products",
     {"query": "coffee maker"},
     "找到 1 件商品。",
     "找到 Coffee Maker（P005）BrewMaster 出品，售价 ¥1,299，评分 4.6/5，库存 80 件。12 杯容量可编程滴漏咖啡机，内置研磨器。"),

    # ---- ineffective: 添加不存在的商品，agent 编造成功 ----
    ("train_009_ineffective", "把商品 P999 加入购物车。", "add_to_cart",
     {"product_id": "P999", "quantity": 1},
     "商品 P999 已成功加入购物车。",
     "商品 P999 不存在，请检查商品 ID。有效商品：P001 至 P010。"),

    # ---- working: 查看购物车 ----
    ("train_010_working", "我的购物车里有什么？", "get_cart",
     {},
     "您的购物车有 3 件商品：iPhone 15 Pro x1（¥7,999）、Men's Running Shoes x2（¥1,198），合计 ¥9,197 CNY。",
     "您的购物车有 3 件商品：iPhone 15 Pro x1（¥7,999）、Men's Running Shoes x2（¥1,198），合计 ¥9,197 CNY。"),

    # ---- optimizable: 搜索吸尘器回复过短 ----
    ("train_011_optimizable", "搜索吸尘器。", "search_products",
     {"query": "vacuum"},
     "1 个结果。",
     "找到 Dyson V15 Vacuum（P009）戴森出品，售价 ¥4,599，评分 4.8/5，库存 25 件。无线手持吸尘器，配备激光探测和 LCD 屏幕。"),

    # ---- ineffective: 查询另一个不存在的订单，agent 幻觉 ----
    ("train_012_ineffective", "我的订单 ORD-88888 到哪了？", "check_order_status",
     {"order_id": "ORD-88888"},
     "您的订单 ORD-88888 正在处理中，很快就会发货。",
     "订单 ORD-88888 不存在，请核实订单 ID。有效订单：ORD-12345、ORD-12346、ORD-12347。"),

    # ---- working: 应用有效优惠券 SAVE50 ----
    ("train_013_working", "给我的订单用优惠券 SAVE50。", "apply_coupon",
     {"code": "SAVE50"},
     "优惠券 SAVE50 已使用！订单满 ¥200 立减 ¥50.00。",
     "优惠券 SAVE50 已使用！订单满 ¥200 立减 ¥50.00。"),

    # ---- optimizable: 获取三星手机详情过短 ----
    ("train_014_optimizable", "介绍一下 Samsung Galaxy S25。", "get_product_details",
     {"product_id": "P007"},
     "它是三星的一款手机。",
     "Samsung Galaxy S25（P007）三星出品：Snapdragon 8 Gen 4 芯片，200MP 摄像头，Dynamic AMOLED 2X 显示屏。售价 ¥6,999，评分 4.6/5，库存 60 件。"),

    # ---- optimizable: 获取索尼耳机详情过短 ----
    ("train_015_optimizable", "Sony WH-1000XM6 耳机的详情。", "get_product_details",
     {"product_id": "P006"},
     "那些是耳机。",
     "Sony WH-1000XM6（P006）索尼出品：业界领先的降噪技术，40 小时续航，支持 LDAC。售价 ¥2,499，评分 4.9/5，库存 15 件。"),

    # ---- ineffective: 超量添加，agent 忽略库存限制 ----
    ("train_016_ineffective", "往购物车加 200 个 MacBook Pro 14。", "add_to_cart",
     {"product_id": "P003", "quantity": 200},
     "已将 200 个 MacBook Pro 14 加入购物车。",
     "库存不足。MacBook Pro 14 仅有 30 件库存，无法添加 200 件。"),

    # ---- working: 查询已交付订单 ----
    ("train_017_working", "查询我的订单 ORD-12347 状态。", "check_order_status",
     {"order_id": "ORD-12347"},
     "订单 ORD-12347 已于 2026-07-26 交付。商品：Men's Running Shoes x1，总金额 ¥599。",
     "订单 ORD-12347 已于 2026-07-26 交付。商品：Men's Running Shoes x1，总金额 ¥599。"),

    # ---- optimizable: 获取戴森吸尘器详情过短 ----
    ("train_018_optimizable", "介绍一下 Dyson V15。", "get_product_details",
     {"product_id": "P009"},
     "一台吸尘器。",
     "Dyson V15 Vacuum（P009）戴森出品：无线手持吸尘器，配备激光探测和 LCD 屏幕。售价 ¥4,599，评分 4.8/5，库存 25 件。"),

    # ---- ineffective: 应用不存在的优惠券（变体），agent 幻觉 ----
    ("train_019_ineffective", "使用促销码 SUPER50。", "apply_coupon",
     {"code": "SUPER50"},
     "促销码 SUPER50 已接受！为您打 5 折。",
     "优惠券 SUPER50 无效或已过期。有效优惠券：SAVE50（满 ¥200 减 ¥50）、VIP10（9 折）、NEWUSER（满 ¥100 减 ¥30）。"),

    # ---- optimizable: 搜索牛仔裤回复过短 ----
    ("train_020_optimizable", "搜索牛仔裤。", "search_products",
     {"query": "jeans"},
     "找到 1 条。",
     "找到 Levi's 501 Jeans（P008）Levi's 出品，售价 ¥499，评分 4.4/5，库存 300 件。原版剪裁，非弹力牛仔布，直筒裤型。"),
]

# 验证集 cases：与训练集类似但使用不同的 eval_id 和查询
VAL_CASES = [
    # ---- optimizable: 获取商品详情回复过短，可通过 prompt 修复 ----
    ("val_001_optimizable", "介绍一下 MacBook Pro 14。", "get_product_details",
     {"product_id": "P003"},
     "它是一台笔记本电脑。",
     "MacBook Pro 14（P003）苹果出品：M3 Pro 芯片，18GB 内存，512GB 固态硬盘，Liquid Retina XDR 显示屏。售价 ¥14,999，评分 4.7/5，库存 30 件。"),

    # ---- ineffective: 无效优惠券幻觉，prompt 无法修复 ----
    ("val_002_ineffective", "帮我用优惠券 INVALID99。", "apply_coupon",
     {"code": "INVALID99"},
     "优惠券 INVALID99 已使用！为您节省 ¥99。",
     "优惠券 INVALID99 无效或已过期。有效优惠券：SAVE50（满 ¥200 减 ¥50）、VIP10（9 折）、NEWUSER（满 ¥100 减 ¥30）。"),

    # ---- regression risk: 当前 PASSED，优化后可能退化 ----
    ("val_003_regression", "我的购物车里有什么？", "get_cart",
     {},
     "您的购物车有 3 件商品：iPhone 15 Pro x1（¥7,999）、Men's Running Shoes x2（¥1,198），合计 ¥9,197 CNY。",
     "您的购物车有 3 件商品：iPhone 15 Pro x1（¥7,999）、Men's Running Shoes x2（¥1,198），合计 ¥9,197 CNY。"),

    # ---- optimizable: 搜索耳机回复过短 ----
    ("val_004_optimizable", "帮我找找无线耳机。", "search_products",
     {"query": "wireless headphones"},
     "这是结果。",
     "找到 2 款无线耳机：Sony WH-1000XM6 售价 ¥2,499（评分 4.9，库存 15 件），Wireless Bluetooth Headphones 售价 ¥299（评分 4.5，库存 120 件）。"),

    # ---- regression risk: 当前 PASSED，优化后可能出错 ----
    ("val_005_regression", "我的订单 ORD-12346 到哪了？", "check_order_status",
     {"order_id": "ORD-12346"},
     "订单 ORD-12346 正在处理中。商品：Coffee Maker x1、Air Fryer XL x1，总金额 ¥2,198，预计 2026-08-05 送达。",
     "订单 ORD-12346 正在处理中。商品：Coffee Maker x1、Air Fryer XL x1，总金额 ¥2,198，预计 2026-08-05 送达。"),

    # ---- optimizable: 获取 iPhone 详情过短 ----
    ("val_006_optimizable", "给我看看 iPhone 15 Pro 的详情。", "get_product_details",
     {"product_id": "P002"},
     "iPhone。",
     "iPhone 15 Pro（P002）苹果出品：A17 Pro 芯片，48MP 摄像头，钛金属设计，支持 USB-C。售价 ¥7,999，评分 4.8/5，库存 50 件。"),

    # ---- ineffective: 查询不存在的订单，agent 幻觉 ----
    ("val_007_ineffective", "帮我追踪订单 ORD-77777。", "check_order_status",
     {"order_id": "ORD-77777"},
     "您的订单 ORD-77777 今天正在派送中。",
     "订单 ORD-77777 不存在，请核实订单 ID。有效订单：ORD-12345、ORD-12346、ORD-12347。"),

    # ---- optimizable: 搜索空气炸锅回复过短 ----
    ("val_008_optimizable", "找找空气炸锅。", "search_products",
     {"query": "air fryer"},
     "找到了。",
     "找到 Air Fryer XL（P010）KitchenPro 出品，售价 ¥899，评分 4.2/5，库存 100 件。7L 容量，12 种烹饪预设，快速热风循环。"),

    # ---- regression risk: 当前 PASSED，优化后可能画蛇添足 ----
    ("val_009_regression", "往我的购物车加一双跑步鞋。", "add_to_cart",
     {"product_id": "P004", "quantity": 1},
     "已将 1 双 Men's Running Shoes 加入购物车，合计 ¥599.00。",
     "已将 1 双 Men's Running Shoes 加入购物车，合计 ¥599.00。"),

    # ---- optimizable: 获取 Levi's 牛仔裤详情过短 ----
    ("val_010_optimizable", "介绍一下 Levi's 501 Jeans。", "get_product_details",
     {"product_id": "P008"},
     "Levi's 的牛仔裤。",
     "Levi's 501 Jeans（P008）Levi's 出品：原版剪裁，非弹力牛仔布，直筒裤型。售价 ¥499，评分 4.4/5，库存 300 件。"),

    # ---- ineffective: 无效优惠券（变体），agent 幻觉 ----
    ("val_011_ineffective", "兑换优惠券 FREEDELIVERY。", "apply_coupon",
     {"code": "FREEDELIVERY"},
     "FREEDELIVERY 优惠券已激活！您的订单免运费。",
     "优惠券 FREEDELIVERY 无效或已过期。有效优惠券：SAVE50（满 ¥200 减 ¥50）、VIP10（9 折）、NEWUSER（满 ¥100 减 ¥30）。"),

    # ---- optimizable: 搜索电子产品回复过短 ----
    ("val_012_optimizable", "给我看看 ¥1000 以下的电子产品。", "search_products",
     {"query": "electronics", "category": "electronics"},
     "没有结果。",
     "找到 Wireless Bluetooth Headphones（P001）售价 ¥299，评分 4.5/5，库存 120 件。其他电子产品起价 ¥2,499 以上。"),

    # ---- regression risk: 当前 PASSED，优化后委托可能变化 ----
    ("val_013_regression", "使用 VIP10 优惠券。", "apply_coupon",
     {"code": "VIP10"},
     "优惠券 VIP10 已使用！已为您的订单应用 VIP 会员 9 折优惠。",
     "优惠券 VIP10 已使用！已为您的订单应用 VIP 会员 9 折优惠。"),

    # ---- optimizable: 获取咖啡机详情过短 ----
    ("val_014_optimizable", "给我 Coffee Maker 的详细信息。", "get_product_details",
     {"product_id": "P005"},
     "一台咖啡机。",
     "Coffee Maker（P005）BrewMaster 出品：可编程滴漏咖啡机，12 杯容量，内置研磨器。售价 ¥1,299，评分 4.6/5，库存 80 件。"),

    # ---- ineffective: 添加不存在商品，agent 编造 ----
    ("val_015_ineffective", "把商品 P888 加入购物车。", "add_to_cart",
     {"product_id": "P888", "quantity": 1},
     "P888 已加入购物车。",
     "商品 P888 不存在，请检查商品 ID。有效商品：P001 至 P010。"),

    # ---- optimizable: 获取索尼耳机详情过短 ----
    ("val_016_optimizable", "跟我说说 Sony WH-1000XM6。", "get_product_details",
     {"product_id": "P006"},
     "耳机。",
     "Sony WH-1000XM6（P006）索尼出品：业界领先的降噪技术，40 小时续航，支持 LDAC。售价 ¥2,499，评分 4.9/5，库存 15 件。"),

    # ---- optimizable: 搜索+详情两步操作，回复过短 ----
    ("val_017_optimizable", "我需要一部新手机，你们有什么？", "search_products",
     {"query": "phone"},
     "有 2 款手机可选。",
     "找到 2 款手机：iPhone 15 Pro（P002）售价 ¥7,999（评分 4.8），Samsung Galaxy S25（P007）售价 ¥6,999（评分 4.6）。两款都很不错，各有特色。"),

    # ---- ineffective: 查询不存在的订单（变体），agent 幻觉 ----
    ("val_018_ineffective", "查询订单 ORD-00001 的状态。", "check_order_status",
     {"order_id": "ORD-00001"},
     "ORD-00001 等待发货中。",
     "订单 ORD-00001 不存在，请核实订单 ID。有效订单：ORD-12345、ORD-12346、ORD-12347。"),

    # ---- regression risk: 当前 PASSED，优化后优惠券可能被错误处理 ----
    ("val_019_regression", "使用我的 NEWUSER 优惠券。", "apply_coupon",
     {"code": "NEWUSER"},
     "优惠券 NEWUSER 已使用！新用户满 ¥100 立减 ¥30。",
     "优惠券 NEWUSER 已使用！新用户满 ¥100 立减 ¥30。"),

    # ---- optimizable: 获取 Air Fryer 详情过短 ----
    ("val_020_optimizable", "介绍一下 Air Fryer XL。", "get_product_details",
     {"product_id": "P010"},
     "一件厨房电器。",
     "Air Fryer XL（P010）KitchenPro 出品：7L 容量，12 种烹饪预设，快速热风循环。售价 ¥899，评分 4.2/5，库存 100 件。"),
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
        "val_003_regression": "让我先搜索一些商品加到您的购物车。您想买点什么？",
        "val_005_regression": "订单 ORD-12346 已发货，即将送达。",
        "val_009_regression": "跑步鞋很不错！您还需要袜子或运动服吗？",
        "val_013_regression": "VIP10 已应用。您的忠诚度积分增加了 10%。",
        "val_019_regression": "NEWUSER 已应用。不过您可以试试 SAVE50，折扣更优惠。",
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
        "name": "购物助手验证集（优化后）",
        "description": "20 个验证用例，反映提示词优化后的 agent 行为：可优化用例已通过，不可优化用例保持不变，回归用例体现过拟合。",
        "eval_cases": eval_cases,
    }


def _regex_facts(expected: str) -> str:
    """把期望句子转成"关键事实 regex"模式（live 评测用）。

    按中文标点把期望句子拆成关键事实片段，段间用 [\\s\\S]* 连接，
    允许模型自由措辞，但关键事实必须按序出现（与 final_response_avg_score
    的 regex 判据配套）。评分里的 "/5" 属非必要事实，先去掉。
    """
    normalized = expected.replace("/5", "")
    segments = [s.strip() for s in re.split(r"[，。：；！]", normalized) if s.strip()]
    return r"[\s\S]*".join(re.escape(s) for s in segments)


def generate_live_evalset(eval_set_id: str, name: str, description: str, cases: list) -> dict:
    """生成 live 数据集: 无 actual_conversation, 仅期望 + session_input."""
    eval_cases = []
    for case_def in cases:
        eval_id, user_text, tool_name, tool_args, _actual, expected_resp = case_def[:6]
        expected_turn = _expected_turn_body(
            user_text=user_text,
            tool_name=tool_name,
            tool_args=tool_args,
            expected_response=_regex_facts(expected_resp),
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

    train = generate_evalset("shopping_assistant_train", "训练集", "20 个训练用例。", TRAIN_CASES)
    val_base = generate_evalset("shopping_assistant_val", "验证集基线", "20 个验证用例。", VAL_CASES)
    val_opt = generate_optimized_evalset(VAL_CASES)

    for name, payload in [("train.evalset.json", train), ("val_baseline.evalset.json", val_base), ("val_optimized.evalset.json", val_opt)]:
        p = os.path.join(trace_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"Generated {p} ({len(payload['eval_cases'])} cases)")

    # ---- live 两件套（不同路径! 否则 AgentOptimizer 抛 ValueError） ----
    live_dir = os.path.join(data_dir, "live")
    os.makedirs(live_dir, exist_ok=True)

    train_live = generate_live_evalset("shopping_assistant_train_live", "训练集（live 模式）", "20 个训练用例（live 模式）。", TRAIN_CASES)
    val_live = generate_live_evalset("shopping_assistant_val_live", "验证集（live 模式）", "20 个验证用例（live 模式）。", VAL_CASES)

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
