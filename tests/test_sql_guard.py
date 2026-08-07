"""Tests for the read-only boundary in mcp_client.sanitize_sql."""

import pytest

from mcp_client import ToolboxError, sanitize_sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders LIMIT 5",
        "SELECT * FROM orders WHERE note ILIKE '%delete%' LIMIT 5",  # keyword in a literal
        "SELECT * FROM products WHERE name = 'A;B' LIMIT 5",         # semicolon in a literal
        "/* header */ SELECT 1 LIMIT 1",                             # leading comment
        "SELECT * FROM call_logs LIMIT 5",                           # keyword inside an identifier
        "WITH x AS (SELECT 1) SELECT * FROM x LIMIT 1",
        "EXPLAIN SELECT * FROM orders",
    ],
)
def test_allows_read_only_queries(sql):
    assert sanitize_sql(sql) == sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE orders",
        "DELETE FROM orders",
        "WITH x AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM x",
        "/* hide */ DROP TABLE orders",
        "SELECT * INTO backup FROM orders",
        "SELECT pg_sleep(60)",
        "",
    ],
)
def test_rejects_writes_and_abuse(sql):
    with pytest.raises(ToolboxError):
        sanitize_sql(sql)


def test_uncapped_select_is_row_capped():
    capped = sanitize_sql("SELECT * FROM orders")
    assert "LIMIT" in capped.upper() and "SELECT * FROM orders" in capped


def test_trailing_semicolon_does_not_break_the_cap():
    assert sanitize_sql("SELECT * FROM orders;").rstrip().endswith(("500", "0"))
