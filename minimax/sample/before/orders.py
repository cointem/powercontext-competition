"""Order query used by the warehouse's order list."""


def list_orders(orders, *, customer_id=None, status=None, limit=20):
    """Return owned orders in ascending ID order, without modifying input."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    selected = [dict(order) for order in orders
                if customer_id is None or order["customer_id"] == customer_id]
    return sorted(selected, key=lambda order: order["id"])[:limit]
