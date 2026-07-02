from billing import total


def daily_summary(orders):
    """Total revenue across today's orders."""
    return sum(total(o.items) for o in orders)
