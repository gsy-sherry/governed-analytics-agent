"""Generate a reproducible SQLite dataset for the analytics prototype."""

import argparse
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from random import Random

DEFAULT_SEED = 42
HISTORY_START = date(2025, 1, 1)
REPORTING_DATE = date(2025, 6, 30)
APPLICATION_ID = 0x47414131
TABLES = ("customers", "checkout_sessions", "orders", "payments")

SCHEMA = """
DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS checkout_sessions;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    signup_date TEXT NOT NULL,
    country TEXT NOT NULL,
    customer_segment TEXT NOT NULL CHECK (customer_segment IN ('standard', 'premium')),
    customer_status TEXT NOT NULL CHECK (customer_status IN ('active', 'inactive')),
    is_test_account INTEGER NOT NULL CHECK (is_test_account IN (0, 1))
);

CREATE TABLE checkout_sessions (
    session_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers (customer_id),
    session_date TEXT NOT NULL,
    checkout_status TEXT NOT NULL CHECK (checkout_status IN ('completed', 'abandoned', 'failed')),
    device_type TEXT NOT NULL CHECK (device_type IN ('iOS', 'Android', 'Web')),
    UNIQUE (session_id, customer_id)
);

CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers (customer_id),
    session_id INTEGER NOT NULL UNIQUE REFERENCES checkout_sessions (session_id),
    order_date TEXT NOT NULL,
    order_amount INTEGER NOT NULL CHECK (typeof(order_amount) = 'integer' AND order_amount > 0),
    order_status TEXT NOT NULL CHECK (order_status IN ('completed', 'cancelled', 'refunded')),
    FOREIGN KEY (session_id, customer_id) REFERENCES checkout_sessions (session_id, customer_id)
);

CREATE TABLE payments (
    payment_id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders (order_id),
    payment_date TEXT NOT NULL,
    payment_amount INTEGER NOT NULL CHECK (typeof(payment_amount) = 'integer' AND payment_amount > 0),
    payment_status TEXT NOT NULL CHECK (payment_status IN ('successful', 'failed')),
    payment_method TEXT NOT NULL CHECK (payment_method IN ('card', 'digital_wallet', 'bank_transfer'))
);

CREATE INDEX checkout_sessions_customer_idx ON checkout_sessions (customer_id);
CREATE INDEX orders_customer_idx ON orders (customer_id);
CREATE INDEX payments_order_idx ON payments (order_id);
"""


def _populate(connection: sqlite3.Connection, rng: Random) -> dict[str, int]:
    customer_rows = []
    for customer_id in range(1, 1_001):
        signup_date = HISTORY_START + timedelta(days=rng.randint(0, (REPORTING_DATE - HISTORY_START).days))
        country = rng.choices(("AU", "NZ", "GB", "US"), (76, 12, 7, 5))[0]
        segment = rng.choices(("standard", "premium"), (79, 21))[0]
        status = rng.choices(("active", "inactive"), (92, 8))[0]
        customer_rows.append((customer_id, signup_date, country, segment, status, int(rng.random() < 0.02)))

    session_rows = []
    order_rows = []
    payment_rows = []
    for session_id in range(1, 5_001):
        customer_id, signup_date, _, segment, _, _ = rng.choice(customer_rows)
        days_remaining = (REPORTING_DATE - signup_date).days
        session_date = signup_date + timedelta(days=rng.randint(0, days_remaining))
        checkout_status = rng.choices(("completed", "abandoned", "failed"), (60, 33, 7))[0]
        device = rng.choices(("iOS", "Android", "Web"), (42, 34, 24))[0]
        session_rows.append((session_id, customer_id, session_date.isoformat(), checkout_status, device))
        if checkout_status != "completed":
            continue

        order_id = len(order_rows) + 1
        order_date = session_date
        order_amount = rng.randint(500, 20_000 if segment == "standard" else 60_000)
        order_status = rng.choices(("completed", "cancelled", "refunded"), (88, 8, 4))[0]
        order_rows.append((order_id, customer_id, session_id, order_date.isoformat(), order_amount, order_status))

        attempt_count = rng.choices((1, 2, 3, 4), (35, 38, 20, 7))[0]
        payment_date = order_date
        for attempt in range(1, attempt_count + 1):
            payment_method = rng.choices(("card", "digital_wallet", "bank_transfer"), (64, 30, 6))[0]
            payment_status = "successful" if attempt == attempt_count and order_status != "cancelled" else "failed"
            payment_rows.append(
                (len(payment_rows) + 1, order_id, payment_date.isoformat(), order_amount, payment_status, payment_method)
            )
            payment_date = min(REPORTING_DATE, payment_date + timedelta(days=rng.randint(0, 2)))

    connection.executemany(
        "INSERT INTO customers VALUES (?, ?, ?, ?, ?, ?)",
        [(customer_id, signup.isoformat(), country, segment, status, is_test)
         for customer_id, signup, country, segment, status, is_test in customer_rows],
    )
    connection.executemany("INSERT INTO checkout_sessions VALUES (?, ?, ?, ?, ?)", session_rows)
    connection.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?)", order_rows)
    connection.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?)", payment_rows)
    return dict(zip(TABLES, map(len, (customer_rows, session_rows, order_rows, payment_rows))))


def generate_database(path: Path, seed: int = DEFAULT_SEED) -> dict[str, int]:
    """Create or regenerate a database owned by this generator."""
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        # Check ownership and rebuild under one write transaction.
        connection.execute("BEGIN IMMEDIATE")
        with connection:
            application_id = connection.execute("PRAGMA application_id").fetchone()[0]
            has_objects = connection.execute("SELECT 1 FROM sqlite_schema LIMIT 1").fetchone()
            if application_id != APPLICATION_ID and (application_id or has_objects):
                raise ValueError("Output database is already in use by another application")
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    connection.execute(statement)
            counts = _populate(connection, Random(seed))
            connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("synthetic.sqlite3"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    try:
        counts = generate_database(args.output, args.seed)
    except (sqlite3.Error, ValueError) as error:
        parser.exit(1, f"{error}\n")
    print(f"Generated {args.output} with seed {args.seed}")
    for table, count in counts.items():
        print(f"{table}: {count:,}")


if __name__ == "__main__":
    main()
