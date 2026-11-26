from contextlib import closing
import sqlite3

import pytest

from governed_analytics.synthetic_data import generate_database


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "synthetic.sqlite3"
    generate_database(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        yield connection


def test_tables_and_approximate_counts(database):
    expected = {"customers": 1000, "checkout_sessions": 5000, "orders": 3000, "payments": 6000}
    tables = {row[0] for row in database.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")}
    assert tables == expected.keys()
    for table, target in expected.items():
        count = database.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
        assert count == pytest.approx(target, rel=0.1)


@pytest.mark.parametrize(
    "insert",
    [
        "INSERT INTO customers SELECT * FROM customers WHERE customer_id = 1",
        "INSERT INTO checkout_sessions VALUES (9001, 9001, '2025-01-01', 'completed', 'Web')",
        "INSERT INTO payments VALUES (9001, 9001, '2025-01-01', 500, 'failed', 'card')",
        "INSERT INTO orders SELECT 9001, customer_id, session_id, order_date, order_amount, order_status "
        "FROM orders WHERE order_id = 1",
        "INSERT INTO orders SELECT 9001, customer_id % 1000 + 1, session_id, session_date, 500, 'completed' "
        "FROM checkout_sessions WHERE session_id NOT IN (SELECT session_id FROM orders) LIMIT 1",
    ],
)
def test_database_rejects_integrity_violations(database, insert):
    with pytest.raises(sqlite3.IntegrityError):
        database.execute(insert)


def test_existing_rows_have_valid_keys_and_customers(database):
    assert database.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    assert database.execute("PRAGMA foreign_key_check").fetchall() == []
    invalid_orders = database.execute("""
        SELECT o.order_id FROM orders AS o
        JOIN checkout_sessions AS s USING (session_id)
        WHERE o.customer_id != s.customer_id OR s.checkout_status != 'completed'
    """).fetchall()
    assert invalid_orders == []


def test_same_seed_reproduces_all_rows_after_regeneration(tmp_path):
    first = tmp_path / "first.sqlite3"
    second = tmp_path / "second.sqlite3"
    generate_database(first, seed=37)
    generate_database(second, seed=51)
    generate_database(second, seed=37)
    with closing(sqlite3.connect(first)) as left, closing(sqlite3.connect(second)) as right:
        assert list(left.iterdump()) == list(right.iterdump())


def test_generation_preserves_unrelated_database(tmp_path):
    path = tmp_path / "unrelated.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
        with pytest.raises(ValueError, match="already in use"):
            generate_database(path)
        assert connection.execute("SELECT name FROM sqlite_schema").fetchall() == [("unrelated",)]


def test_chronology(database):
    invalid_dates = database.execute("""
        SELECT customer_id FROM customers
        WHERE signup_date NOT BETWEEN '2025-01-01' AND '2025-06-30'
        UNION ALL
        SELECT session_id FROM checkout_sessions JOIN customers USING (customer_id)
        WHERE session_date NOT BETWEEN signup_date AND '2025-06-30'
        UNION ALL
        SELECT order_id FROM orders JOIN checkout_sessions USING (session_id)
        WHERE order_date NOT BETWEEN session_date AND '2025-06-30'
        UNION ALL
        SELECT payment_id FROM payments JOIN orders USING (order_id)
        WHERE payment_date NOT BETWEEN order_date AND '2025-06-30'
    """).fetchall()
    assert invalid_dates == []


def test_payment_attempts_and_test_accounts(database):
    retry_count = database.execute("""
        SELECT count(*) FROM (SELECT order_id FROM payments GROUP BY order_id HAVING count(*) > 1)
    """).fetchone()[0]
    test_count, customer_count = database.execute(
        "SELECT sum(is_test_account), count(*) FROM customers"
    ).fetchone()
    assert retry_count > 0
    assert 0 < test_count < customer_count * 0.05


def test_basic_analytics(database):
    segment_counts = database.execute("""
        SELECT c.customer_segment, count(*),
               sum(CASE WHEN s.checkout_status = 'completed' THEN 1 ELSE 0 END)
        FROM checkout_sessions AS s JOIN customers AS c USING (customer_id)
        WHERE c.is_test_account = 0
        GROUP BY c.customer_segment
    """).fetchall()
    assert {row[0] for row in segment_counts} == {"standard", "premium"}
    assert all(0 < completed < total for _, total, completed in segment_counts)

    payment_totals = database.execute("""
        SELECT count(*), sum(payment_amount)
        FROM payments JOIN orders USING (order_id)
        WHERE payment_status = 'successful' AND order_status = 'completed'
    """).fetchone()
    order_totals = database.execute("""
        SELECT count(*), sum(order_amount) FROM orders WHERE order_status = 'completed'
    """).fetchone()
    assert payment_totals == order_totals
    assert payment_totals[1] > 0
