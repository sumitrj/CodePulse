from pricing import compute_total


def process(order):
    """Validate, price, and persist an order."""
    if not order.items:
        raise ValueError("empty order")
    order.amount = compute_total(order.items)
    persist(order)
    return order


def persist(order):
    db.write(order)
