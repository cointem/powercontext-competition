# Order query specification — revision 1

## ORD-001: Preserve existing order query behavior
`orders.py:list_orders` returns dictionaries ordered by ascending integer `id`.
`customer_id=None` selects all customers; a supplied customer ID selects only that customer.
`limit` defaults to 20, is a nonnegative integer (not bool), and applies after filtering and sorting.
Invalid limits raise ValueError. Zero returns an empty list. Do not mutate the input list or its dictionaries.
No status filter preserves existing behavior, including legacy orders whose status is absent.

## ORD-002: Add order status filtering
`status=None` means no status filter. Otherwise accept exactly the strings
`pending`, `paid`, `shipped`, `cancelled`; filter by exact equality to an order's status.
Other values (including empty strings, uppercase, integers and lists) raise ValueError,
even when input is empty. Orders with missing status do not match an explicit filter.
Combine customer and status filters with AND, then sort, then limit.

## ORD-003: Validation sources
`tests/test_orders_feature.py` verifies status filtering and invalid input.
`tests/test_orders_regression.py` preserves customer, ordering, limit and nonmutation contracts.
