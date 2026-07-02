from billing import total


def process(order):
    """Validate, price, persist, and audit an order."""
    if not order.items:
        raise ValueError("empty order")
    order.amount = total(order.items)
    db.write(order)
    audit.log(order)
    return order
