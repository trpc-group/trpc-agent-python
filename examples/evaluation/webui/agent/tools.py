# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Tools for the book finder agent."""

from typing import Any, Dict


def search_local_library(book_title: str) -> Dict[str, Any]:
    """查询本地图书馆的书籍可用性，包括副本数量、所在分馆和位置信息。

    参数:
        book_title: 书籍标题

    返回:
        包含书籍可用性信息的字典
    """
    library_data = {
        "Heartstopper: Volume 1": {
            "available": False,
            "copies": 0,
            "branch": "中央图书馆",
            "status": "所有副本都已借出",
        },
        "三体": {
            "available": True,
            "copies": 3,
            "branch": "科技图书馆",
            "status": "有 3 本可借",
            "location": "科幻小说区 A-301",
        },
        "活着": {
            "available": True,
            "copies": 2,
            "branch": "文学图书馆",
            "status": "有 2 本可借",
            "location": "现代文学区 B-205",
        },
        "Python编程：从入门到实践": {
            "available": True,
            "copies": 1,
            "branch": "技术图书馆",
            "status": "仅剩 1 本可借",
            "location": "编程语言区 C-102",
        },
    }

    result = library_data.get(
        book_title,
        {
            "available": False,
            "copies": 0,
            "branch": "未找到",
            "status": "本地图书馆没有此书",
        },
    )

    return {
        "source": "本地图书馆",
        "book_title": book_title,
        **result,
    }


def find_local_bookstore(book_title: str) -> Dict[str, Any]:
    """查找本地书店的书籍库存信息，包括价格、地址和联系方式。

    参数:
        book_title: 书籍标题

    返回:
        包含书店库存信息的字典
    """
    bookstore_data = {
        "Heartstopper: Volume 1": {
            "available": True,
            "price": "49.80元",
            "store": "新华书店",
            "address": "市中心步行街128号",
            "phone": "0755-12345678",
            "stock": 5,
        },
        "三体": {
            "available": True,
            "price": "23.00元",
            "store": "当当实体书店",
            "address": "科技园区创业路56号",
            "phone": "0755-87654321",
            "stock": 10,
        },
        "解忧杂货店": {
            "available": True,
            "price": "39.50元",
            "store": "方所书店",
            "address": "购物中心3楼",
            "phone": "0755-11223344",
            "stock": 3,
        },
    }

    result = bookstore_data.get(
        book_title,
        {
            "available": False,
            "store": "未找到",
            "status": "本地书店暂无库存",
        },
    )

    return {
        "source": "本地书店",
        "book_title": book_title,
        **result,
    }


def order_online(book_title: str) -> Dict[str, Any]:
    """查找在线购买书籍的选项，提供多个电商平台的信息和配送时间。

    参数:
        book_title: 书籍标题

    返回:
        包含在线购买信息的字典
    """
    online_data = {
        "default": {
            "available": True,
            "platforms": [
                {
                    "name": "京东图书",
                    "price": "根据书籍而定",
                    "url": "https://book.jd.com",
                    "delivery": "次日达（会员）",
                },
                {
                    "name": "当当网",
                    "price": "根据书籍而定",
                    "url": "https://book.dangdang.com",
                    "delivery": "2-3个工作日",
                },
                {
                    "name": "亚马逊中国",
                    "price": "根据书籍而定",
                    "url": "https://www.amazon.cn",
                    "delivery": "2-5个工作日",
                },
            ],
            "recommendation": "建议先搜索比价，选择最优惠的平台购买",
        }
    }

    result = online_data["default"]

    return {
        "source": "在线零售商",
        "book_title": book_title,
        **result,
    }
