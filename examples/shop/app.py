import os

from billing import total


def checkout(cart):
    """Charge a cart: price it, then record the order."""
    return save_order(total(cart))


def save_order(amount):
    return {"db": os.environ["DB_URL"], "amount": amount}


def main():
    print(checkout([{"price": 10, "qty": 2}]))


if __name__ == "__main__":
    main()
