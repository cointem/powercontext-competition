from copy import deepcopy
import pytest
from orders import list_orders


def test_default_and_explicit_none(orders):
    expected = sorted(orders, key=lambda o: o["id"])
    assert list_orders(orders) == expected
    assert list_orders(orders, status=None) == expected


def test_customer_sort_limit(orders):
    assert [o["id"] for o in list_orders(orders, customer_id="alice", limit=2)] == [2, 3]


def test_zero_and_empty(orders):
    assert list_orders(orders, limit=0) == []
    assert list_orders([]) == []


@pytest.mark.parametrize("limit", [-1, 1.5, "2", True, None])
def test_invalid_limit(orders, limit):
    with pytest.raises(ValueError):
        list_orders(orders, limit=limit)


def test_input_and_output_isolation(orders):
    before = deepcopy(orders)
    output = list_orders(orders)
    output[0]["status"] = "changed"
    assert orders == before


def test_default_limit():
    items = [{"id": i, "customer_id": "a"} for i in range(30, 0, -1)]
    assert [o["id"] for o in list_orders(items)] == list(range(1, 21))
