# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tools for the book finder agent."""

from typing import Any
from typing import Dict


def search_local_library(book_title: str) -> Dict[str, Any]:
    """Search for the availability of books in the local library, including the number of copies, the branch and location information.

    Args:
        book_title: Book title

    Returns:
        Dictionary containing book availability information
    """
    library_data = {
        "Heartstopper: Volume 1": {
            "available": False,
            "copies": 0,
            "branch": "Central Library",
            "status": "All copies have been borrowed",
        },
        "Three-Body Problem": {
            "available": True,
            "copies": 3,
            "branch": "Science Library",
            "status": "There are 3 copies available",
            "location": "Science Fiction Area A-301",
        },
        "Living": {
            "available": True,
            "copies": 2,
            "branch": "Literature Library",
            "status": "There are 2 copies available",
            "location": "Modern Literature Area B-205",
        },
        "Python Programming: From Beginner to Practice": {
            "available": True,
            "copies": 1,
            "branch": "Technology Library",
            "status": "Only 1 copy available",
            "location": "Programming Language Area A-102",
        },
    }

    result = library_data.get(
        book_title,
        {
            "available": False,
            "copies": 0,
            "branch": "Not found",
            "status": "Local library has no this book.",
        },
    )

    return {
        "source": "Local library",
        "book_title": book_title,
        **result,
    }


def find_local_bookstore(book_title: str) -> Dict[str, Any]:
    """Find the inventory information of books in the local bookstore, including price, address and contact information.

    Args:
        book_title: Book title

    Returns:
        Dictionary containing bookstore inventory information
    """
    bookstore_data = {
        "Heartstopper: Volume 1": {
            "available": True,
            "price": "49.80￥",
            "store": "Xinhua Bookshop",
            "address": "Central Business District, Walking Street 128",
            "phone": "0755-12345678",
            "stock": 5,
        },
        "Three-Body Problem": {
            "available": True,
            "price": "23.00￥",
            "store": "Dangdang Bookshop",
            "address": "Science Park, Entrepreneur Road 56",
            "phone": "0755-87654321",
            "stock": 10,
        },
        "The Store of Good Wishes": {
            "available": True,
            "price": "39.50￥",
            "store": "Fangsuo Bookstore",
            "address": "Shopping Center, 3rd Floor",
            "phone": "0755-11223344",
            "stock": 3,
        },
    }

    result = bookstore_data.get(
        book_title,
        {
            "available": False,
            "store": "Not found",
            "status": "Local bookstore has no inventory",
        },
    )

    return {
        "source": "Local bookstore",
        "book_title": book_title,
        **result,
    }


def order_online(book_title: str) -> Dict[str, Any]:
    """Find online purchase options, provide information and delivery time for multiple e-commerce platforms.

    Args:
        book_title: Book title

    Returns:
        Dictionary containing online purchase information
    """
    online_data = {
        "default": {
            "available":
            True,
            "platforms": [
                {
                    "name": "JD Books",
                    "price": "Depends on the book",
                    "url": "https://book.jd.com",
                    "delivery": "Next day delivery (VIP)",
                },
                {
                    "name": "Dangdang",
                    "price": "Depends on the book",
                    "url": "https://book.dangdang.com",
                    "delivery": "2-3 days",
                },
                {
                    "name": "Amazon China",
                    "price": "Depends on the book",
                    "url": "https://www.amazon.cn",
                    "delivery": "2-5 days",
                },
            ],
            "recommendation":
            "Suggest searching for price comparison and choosing the most affordable platform to purchase",
        }
    }

    result = online_data["default"]

    return {
        "source": "Online retailer",
        "book_title": book_title,
        **result,
    }
