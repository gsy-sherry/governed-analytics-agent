"""Independent Python aggregation for reproducible golden numerical results."""

import argparse
import sqlite3
from collections import defaultdict
from contextlib import closing
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluation.dataset import DATASET_PATH, ExpectedOutcome, ExpectedResult, load_dataset
from governed_analytics.synthetic_data import REPORTING_DATE, generate_database


class ReferenceData:
    def __init__(self, path: Path):
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
            connection.row_factory = sqlite3.Row
            self.customers = {row["customer_id"]: row for row in connection.execute("SELECT * FROM customers")}
            self.sessions = list(connection.execute("SELECT * FROM checkout_sessions"))
            self.orders = {row["order_id"]: row for row in connection.execute("SELECT * FROM orders")}
            self.payments = list(connection.execute("SELECT * FROM payments"))

    def _events(self, metric_id):
        if metric_id == "new_customers":
            for customer in self.customers.values():
                yield customer["signup_date"], customer, customer
        elif metric_id in {"checkout_volume", "checkout_conversion_rate"}:
            for session in self.sessions:
                yield session["session_date"], session, self.customers[session["customer_id"]]
        elif metric_id in {"active_customers", "gmv", "average_order_value"}:
            for order in self.orders.values():
                if order["order_status"] == "completed":
                    yield order["order_date"], order, self.customers[order["customer_id"]]
        elif metric_id in {"payment_volume", "payment_failure_rate"}:
            for attempt in self.payments:
                order = self.orders[attempt["order_id"]]
                yield attempt["payment_date"], attempt, self.customers[order["customer_id"]]
        else:
            raise ValueError("Unknown reference metric")

    def calculate(self, expected: ExpectedOutcome) -> list[ExpectedResult]:
        groups = defaultdict(list)
        if expected.dimension is None:
            groups[None] = []
        start, end = expected.start_date.isoformat(), expected.end_date.isoformat()
        for date, event, customer in self._events(expected.metric_id):
            if customer["is_test_account"] or not start <= date < end:
                continue
            group = None
            if expected.dimension:
                source = customer if expected.dimension in {"country", "customer_segment"} else event
                group = source[expected.dimension]
            groups[group].append(event)
        return [
            ExpectedResult(group=group, value=_render(_aggregate(expected.metric_id, rows)))
            for group, rows in sorted(groups.items(), key=lambda item: item[0] or "")
        ]


def _aggregate(metric_id, rows):
    if metric_id == "active_customers":
        return len({row["customer_id"] for row in rows})
    if metric_id == "gmv":
        return Fraction(sum(row["order_amount"] for row in rows), 100)
    if metric_id == "average_order_value":
        return Fraction(sum(row["order_amount"] for row in rows), 100 * len(rows)) if rows else None
    if metric_id == "checkout_conversion_rate":
        return Fraction(sum(row["checkout_status"] == "completed" for row in rows), len(rows)) if rows else None
    if metric_id == "payment_failure_rate":
        return Fraction(sum(row["payment_status"] == "failed" for row in rows), len(rows)) if rows else None
    return len(rows)


def _render(value):
    if isinstance(value, Fraction):
        with localcontext() as context:
            context.prec = 32
            return str(Decimal(value.numerator) / Decimal(value.denominator))
    return None if value is None else str(value)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Update only the stored numerical results")
    args = parser.parse_args()
    dataset = load_dataset()
    if dataset.reporting_date != REPORTING_DATE:
        parser.error("Dataset and fixture reporting dates differ")
    changed = []
    with TemporaryDirectory() as directory:
        path = Path(directory) / "fixture.sqlite3"
        generate_database(path, dataset.seed)
        reference = ReferenceData(path)
        for case in dataset.cases:
            if case.expected.status == "answered":
                results = reference.calculate(case.expected)
                if results != case.expected.results:
                    changed.append(case.case_id)
                    case.expected.results = results
    if args.write:
        DATASET_PATH.write_text(dataset.model_dump_json(indent=2, exclude_unset=True) + "\n")
    if changed:
        print(f"{'Updated' if args.write else 'Reference mismatch'}: {', '.join(changed)}")
    else:
        print("All golden numerical results match the independent reference.")
    return int(bool(changed) and not args.write)


if __name__ == "__main__":
    raise SystemExit(main())
