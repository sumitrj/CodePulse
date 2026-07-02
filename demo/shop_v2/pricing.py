from config import TAX_RATE


def compute_total(items):
    """Sum of line-item prices, tax included."""
    subtotal = sum(item.price for item in items)
    return subtotal * (1 + TAX_RATE)
