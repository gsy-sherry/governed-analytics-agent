import sqlite3
from contextlib import closing
from dataclasses import fields

import pytest

from governed_analytics.access_policy import can_access_metric
from governed_analytics.knowledge_retrieval import retrieve_metrics
from governed_analytics.metric_registry import METRIC_REGISTRY, TEST_ACCOUNT_FILTER, MetricDefinition
from governed_analytics.synthetic_data import SCHEMA

PERMISSIONS = {
    "Product": {"active_customers", "checkout_volume", "checkout_conversion_rate", "gmv", "average_order_value"},
    "Marketing": {"new_customers", "active_customers", "average_order_value"},
    "Payments": {"payment_volume", "payment_failure_rate"},
}
APPROVED_METRICS = set().union(*PERMISSIONS.values())
PERMISSIONS["Admin"] = APPROVED_METRICS


def test_metric_definitions_are_complete_and_match_the_schema():
    assert len(METRIC_REGISTRY) == len(APPROVED_METRICS) == 8
    assert METRIC_REGISTRY.keys() == APPROVED_METRICS
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.executescript(SCHEMA)
        for metric_id, metric in METRIC_REGISTRY.items():
            assert all(getattr(metric, field.name) for field in fields(MetricDefinition))
            assert metric.metric_id == metric_id
            columns_by_table = {
                table: {row[1] for row in connection.execute("SELECT * FROM pragma_table_info(?)", (table,))}
                for table in metric.source_tables
            }
            assert all(columns_by_table.values())
            table, date_column = metric.date_column.split(".")
            assert table == metric.source_tables[0]
            assert date_column in columns_by_table[table]
            assert set(metric.allowed_dimensions) <= set().union(*columns_by_table.values())
            assert set(metric.allowed_roles) <= PERMISSIONS.keys()
            assert metric.output_unit in {"count", "ratio", "AUD"}
            assert TEST_ACCOUNT_FILTER in metric.required_filters
            assert "is_test_account" in columns_by_table["customers"]


@pytest.mark.parametrize("role, expected", PERMISSIONS.items())
def test_role_permissions_and_retrieval_visibility(role, expected):
    assert {metric_id for metric_id in METRIC_REGISTRY if can_access_metric(role, metric_id)} == expected
    question = ", ".join(METRIC_REGISTRY)
    assert {metric.metric_id for metric in retrieve_metrics(question, role, top_k=8)} == expected


def test_default_denial():
    assert not can_access_metric("Unknown", "gmv")
    assert retrieve_metrics("GMV", "Unknown") == ()
    assert not can_access_metric("Admin", "revenue")
    assert not can_access_metric("Admin", "SELECT * FROM customers")
    assert not can_access_metric("Admin", "aov")


def test_approved_dimensions():
    approved = {"customer_segment", "country", "device_type", "payment_method"}
    for metric in METRIC_REGISTRY.values():
        assert set(metric.allowed_dimensions) <= approved
        for identifier in ("customer_id", "session_id", "order_id", "payment_id"):
            assert not can_access_metric("Admin", metric.metric_id, dimensions=(identifier,))
    assert can_access_metric("Marketing", "average_order_value", dimensions=("customer_segment",))
    assert can_access_metric("Product", "checkout_volume", dimensions=("device_type",))
    assert can_access_metric("Payments", "payment_failure_rate", dimensions=("payment_method",))
    assert not can_access_metric("Marketing", "average_order_value", dimensions=("payment_method",))


@pytest.mark.parametrize(
    "question, role, expected",
    [
        ("Show sign-ups by country", "Marketing", "new_customers"),
        ("How many unique buyers?", "Product", "active_customers"),
        ("Show checkout attempts by device", "Product", "checkout_volume"),
        ("What was the checkout completion rate?", "Product", "checkout_conversion_rate"),
        ("Gross merchandise value", "Product", "gmv"),
        ("AOV by customer segment", "Marketing", "average_order_value"),
        ("Payment attempts", "Payments", "payment_volume"),
        ("Payment decline rate by method", "Payments", "payment_failure_rate"),
        ("Mean value in AUD per completed order placed during the reporting period.", "Marketing", "average_order_value"),
    ],
)
def test_discovery_by_business_language(question, role, expected):
    assert retrieve_metrics(question, role, top_k=1) == (METRIC_REGISTRY[expected],)


def test_ranking_and_top_k():
    question = "GMV and average basket value"
    assert retrieve_metrics(question, "Admin", top_k=2) == (
        METRIC_REGISTRY["average_order_value"], METRIC_REGISTRY["gmv"],
    )
    assert retrieve_metrics(question, "Admin", top_k=1) == (METRIC_REGISTRY["average_order_value"],)
    assert retrieve_metrics(question, "Admin", top_k=0) == ()
    with pytest.raises(ValueError):
        retrieve_metrics(question, "Admin", top_k=-1)


def test_unsupported_questions_do_not_select_a_metric_for_execution():
    assert retrieve_metrics("What was the refund rate?", "Admin") == ()
    assert retrieve_metrics("Show failed payments by card number", "Admin") == ()
    question = "Show median order value alongside AOV"
    assert retrieve_metrics(question, "Admin") == (METRIC_REGISTRY["average_order_value"],)
    assert not can_access_metric("Admin", "median_order_value")
    assert not can_access_metric("Admin", question)
