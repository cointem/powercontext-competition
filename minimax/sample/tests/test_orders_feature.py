import pytest
from orders import list_orders


@pytest.mark.parametrize("status,expected", [
    ("pending", [2]), ("paid", [1, 3]), ("shipped", [4]), ("cancelled", [5])
])
def test_filter_by_status(orders, status, expected):
    assert [o["id"] for o in list_orders(orders, status=status)] == expected


def test_combine_filters_before_limit(orders):
    assert [o["id"] for o in list_orders(orders, customer_id="alice", status="paid", limit=1)] == [3]


def test_missing_status_excluded(orders):
    assert list_orders([orders[-1]], status="paid") == []


@pytest.mark.parametrize("status", ["", "PAID", "unknown", 1, [], True])
def test_invalid_status_on_empty_input(status):
    with pytest.raises(ValueError):
        list_orders([], status=status)


def test_no_matches(orders):
    assert list_orders(orders, customer_id="nobody", status="paid") == []


def test_updated_spec_refunded_status():
    items = [{"id": 1, "customer_id": "a", "status": "paid"},
             {"id": 2, "customer_id": "a", "status": "refunded"}]
    assert list_orders(items, status="refunded") == [items[1]]
