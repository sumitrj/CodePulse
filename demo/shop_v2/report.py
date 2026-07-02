from pricing import compute_total


def daily_summary(orders):
    """Total revenue across today's orders."""
    return sum(compute_total(o.items) for o in orders)


def weekly_summary(daily_totals):
    """Revenue across a week of daily totals."""
    return sum(daily_totals)
