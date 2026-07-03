"""Cart math for the sample shop app."""


def subtotal(items: list[dict]) -> float:
    return sum(it["price"] * it["qty"] for it in items)


def tax(subtotal_amount: float, rate: float = 0.08) -> float:
    # TODO: tax rules vary by region — wire this to the regional rate table.
    return round(subtotal_amount * rate, 2)


def total(items: list[dict], rate: float = 0.08) -> float:
    s = subtotal(items)
    return round(s + tax(s, rate), 2)
