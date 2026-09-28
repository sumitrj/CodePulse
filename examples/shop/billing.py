TAX_RATE = 0.18


def subtotal(cart):
    return sum(line["price"] * line["qty"] for line in cart)


def total(cart):
    """Subtotal plus tax."""
    return round(subtotal(cart) * (1 + TAX_RATE), 2)
