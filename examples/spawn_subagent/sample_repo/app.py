"""Top-level handlers for the sample shop app."""

from auth import user_of, verify_token
from cart import total
from db import delete_order, load_order, save_order


def login(token: str) -> dict:
    if not verify_token(token):
        return {"ok": False, "reason": "bad token"}
    return {"ok": True, "user": user_of(token)}


def checkout(token: str, items: list[dict], order_id: str) -> dict:
    if not verify_token(token):
        return {"ok": False, "reason": "bad token"}
    amount = total(items)
    save_order(order_id, {"user": user_of(token), "items": items, "amount": amount})
    return {"ok": True, "order_id": order_id, "amount": amount}


def refund(token: str, order_id: str) -> dict:
    if not verify_token(token):
        return {"ok": False, "reason": "bad token"}
    order = load_order(order_id)
    if order is None:
        return {"ok": False, "reason": "no such order"}
    delete_order(order_id)
    return {"ok": True, "refunded": order["amount"]}
