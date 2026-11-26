# Governed Analytics Agent

A CLI prototype for internal self-service analytics using synthetic SQLite data for customers, checkout sessions, orders, and payments.

The planned design uses LangGraph for orchestration and the OpenAI API for intent understanding. A lightweight metric registry and knowledge retrieval inform structured query plans. Deterministic role-based access control governs execution through approved parameterised SQL templates, and structured audit logs record decisions and execution outcomes.

This initial phase provides the Python package, test directory, and local setup files.

## Local setup

Use Python 3.11 or newer. Check the interpreter version before creating the virtual environment.

```sh
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -c "import langgraph.graph, openai, pytest, sqlite3"
```

If `python3` points to an older interpreter, substitute the path to a supported Python executable. Dependency checks require no API key. `.env.example` documents the environment variable for the future OpenAI integration.

## Project layout

| Path | Purpose |
| --- | --- |
| `governed_analytics/` | Application package |
| `tests/` | Pytest test package |
| `requirements.txt` | Runtime and test dependencies |
| `.env.example` | Environment variable template |
| `.gitignore` | Rules for local credentials, environments, and generated files |

## Synthetic SQLite database

From the repository root, run:

```sh
.venv/bin/python -m governed_analytics.synthetic_data
.venv/bin/python -m pytest
```

The generator defaults to seed `42` and writes `synthetic.sqlite3`, which Git ignores. Run the same command to rebuild the dataset. Use `--output another.sqlite3` for a different destination or `--seed 123` for an alternate sample. The generator replaces its own dataset in a transaction and refuses to overwrite databases owned by other applications.

The six-month window runs from 1 January through the fixed reporting date of 30 June 2025, inclusive. Dates use ISO `YYYY-MM-DD` strings, and related events can share a date. The fixture uses synthetic integer IDs and contains no personal details. A small share of customers are flagged with `is_test_account = 1`. All monetary amounts are integers in AUD cents, regardless of country.

| Table | Grain and relationships | Status definitions |
| --- | --- | --- |
| `customers` | One row per synthetic customer. | `active` or `inactive` is the assigned reporting-date status. Both can have historical activity. |
| `checkout_sessions` | One row per checkout attempt by a customer. | `completed` reached order creation, `abandoned` ended before submission, and `failed` encountered a checkout error. |
| `orders` | One row per order, linked to a unique completed session for the same customer. Each completed session generates an order on the session date. | `completed` was paid, `cancelled` ended without a successful payment, and `refunded` was paid and later refunded in full. |
| `payments` | One row per payment attempt. Each order has one to four attempts for the full order amount. | `failed` did not collect funds. `successful` is the final attempt for completed and refunded orders. Refunds are represented by the order status. |

A customer-to-session join repeats customer rows, and an order-to-payment join repeats order rows for retries. Aggregate order amounts at order grain, or aggregate payment attempts by order before joining. Failed attempts contribute to attempt counts. For net collected amounts in this fixture, use successful payments on completed orders, since refunded orders represent full refunds. Filter test accounts through the customer relationship when reporting production-like metrics.

The generator enables SQLite foreign keys. Any client that writes to the database must enable `PRAGMA foreign_keys = ON` on its own connection.
