"""In-memory data store for the sample shop app."""

ORDERS: dict[str, dict] = {}


def save_order(order_id: str, payload: dict) -> None:
    ORDERS[order_id] = payload


def load_order(order_id: str) -> dict | None:
    return ORDERS.get(order_id)


def delete_order(order_id: str) -> bool:
    return ORDERS.pop(order_id, None) is not None
