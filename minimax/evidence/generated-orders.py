"""Order query used by the warehouse's order list."""


_VALID_STATUSES = {"pending", "paid", "shipped", "cancelled", "refunded"}


def list_orders(orders, *, customer_id=None, status=None, limit=20):
    """Return owned orders in ascending ID order, without modifying input."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    if status is not None and (
        not isinstance(status, str) or status not in _VALID_STATUSES
    ):
        raise ValueError(
            "status must be one of: pending, paid, shipped, cancelled, refunded"
        )

    selected = [
        dict(order)
        for order in orders
        if (customer_id is None or order["customer_id"] == customer_id)
        and (status is None or order.get("status") == status)
    ]
    return sorted(selected, key=lambda order: order["id"])[:limit]
