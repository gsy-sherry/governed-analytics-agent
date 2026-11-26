"""Internal SQL templates and read-only execution for approved aggregates."""

import sqlite3
from ast import literal_eval
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from governed_analytics.intent import QueryPlan
from governed_analytics.metric_registry import METRIC_REGISTRY

_MAX_GROUPS = 100

_FROM = {
    "customers": "customers",
    "checkout_sessions": "checkout_sessions JOIN customers USING (customer_id)",
    "orders": "orders JOIN customers USING (customer_id)",
    "payments": "payments JOIN orders USING (order_id) JOIN customers USING (customer_id)",
}

_DIMENSIONS = {
    "customer_segment": "customers.customer_segment",
    "country": "customers.country",
    "device_type": "checkout_sessions.device_type",
    "payment_method": "payments.payment_method",
}

_TEMPLATES = {
    "new_customers": ("COUNT(*), NULL", ()),
    "active_customers": ("COUNT(DISTINCT orders.customer_id), NULL", ()),
    "checkout_volume": ("COUNT(*), NULL", ()),
    "checkout_conversion_rate": (
        "COUNT(CASE WHEN checkout_sessions.checkout_status = ? THEN 1 END), COUNT(*)",
        ("completed",),
    ),
    "gmv": ("COALESCE(SUM(orders.order_amount), 0), NULL", ()),
    "average_order_value": ("COALESCE(SUM(orders.order_amount), 0), COUNT(*)", ()),
    "payment_volume": ("COUNT(*), NULL", ()),
    "payment_failure_rate": (
        "COUNT(CASE WHEN payments.payment_status = ? THEN 1 END), COUNT(*)",
        ("failed",),
    ),
}


@dataclass(frozen=True, slots=True)
class _ApprovedQuery:
    sql: str
    parameters: tuple[str | int, ...]


AggregateRow = tuple[str | None, int, int | None]


def _build_sql(plan: QueryPlan) -> _ApprovedQuery:
    """Build SQL after graph policy validation using code-owned mappings."""
    metric = METRIC_REGISTRY[plan.metric_id]
    aggregate, aggregate_params = _TEMPLATES[metric.metric_id]
    dimension = _DIMENSIONS[plan.dimension] if plan.dimension else None
    group_by = f" GROUP BY {dimension} ORDER BY {dimension}" if dimension else ""
    date_column = metric.date_column

    # Registry filters are trusted, version-controlled column = literal pairs.
    predicates = []
    filter_params = []
    for required_filter in metric.required_filters:
        column, value = required_filter.split(" = ", 1)
        predicates.append(f"{column} = ?")
        filter_params.append(literal_eval(value))
    where = " AND ".join((f"{date_column} >= ?", f"{date_column} < ?", *predicates))
    sql = (
        f"SELECT {dimension or 'NULL'}, {aggregate} FROM {_FROM[metric.source_tables[0]]} "
        f"WHERE {where}{group_by} LIMIT ?"
    )
    return _ApprovedQuery(sql, (
        *aggregate_params, plan.start_date.isoformat(), plan.end_date.isoformat(),
        *filter_params, _MAX_GROUPS + 1,
    ))


def _execute_query(path: Path, query: _ApprovedQuery) -> list[AggregateRow]:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        rows = connection.execute(query.sql, query.parameters).fetchmany(_MAX_GROUPS + 1)
    if len(rows) > _MAX_GROUPS:
        raise ValueError("Aggregate result exceeds group limit")
    return rows
