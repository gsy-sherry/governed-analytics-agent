"""Approved aggregate metric definitions for the synthetic data model."""

from dataclasses import dataclass
from types import MappingProxyType

TEST_ACCOUNT_FILTER = "customers.is_test_account = 0"
COMPLETED_ORDER_FILTER = "orders.order_status = 'completed'"
CUSTOMER_DIMENSIONS = ("customer_segment", "country")
CHECKOUT_DIMENSIONS = (*CUSTOMER_DIMENSIONS, "device_type")
PAYMENT_DIMENSIONS = (*CUSTOMER_DIMENSIONS, "payment_method")
PRODUCT_ROLES = ("Product", "Admin")
CUSTOMER_ACTIVITY_ROLES = ("Product", "Marketing", "Admin")
PAYMENTS_ROLES = ("Payments", "Admin")


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    display_name: str
    business_definition: str
    synonyms: tuple[str, ...]
    calculation: str
    source_tables: tuple[str, ...]
    data_grain: str
    date_column: str
    allowed_dimensions: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    output_unit: str
    required_filters: tuple[str, ...] = (TEST_ACCOUNT_FILTER,)


_METRICS = (
    MetricDefinition(
        metric_id="new_customers",
        display_name="New Customers",
        business_definition="Customers who registered during the reporting period.",
        synonyms=("signups", "sign ups", "customer registrations", "new registrations"),
        calculation="Count customer rows.",
        source_tables=("customers",),
        data_grain="customer",
        date_column="customers.signup_date",
        allowed_dimensions=CUSTOMER_DIMENSIONS,
        allowed_roles=("Marketing", "Admin"),
        output_unit="count",
    ),
    MetricDefinition(
        metric_id="active_customers",
        display_name="Active Customers",
        business_definition="Distinct customers who placed a completed order during the reporting period.",
        synonyms=("purchasing customers", "unique buyers", "distinct buyers"),
        calculation="Count distinct orders.customer_id across completed orders.",
        source_tables=("orders", "customers"),
        data_grain="order",
        date_column="orders.order_date",
        allowed_dimensions=CUSTOMER_DIMENSIONS,
        allowed_roles=CUSTOMER_ACTIVITY_ROLES,
        output_unit="count",
        required_filters=(TEST_ACCOUNT_FILTER, COMPLETED_ORDER_FILTER),
    ),
    MetricDefinition(
        metric_id="checkout_volume",
        display_name="Checkout Volume",
        business_definition="All checkout sessions started during the reporting period, across all statuses.",
        synonyms=("checkout sessions", "checkout attempts", "checkout count"),
        calculation="Count checkout session rows.",
        source_tables=("checkout_sessions", "customers"),
        data_grain="checkout session",
        date_column="checkout_sessions.session_date",
        allowed_dimensions=CHECKOUT_DIMENSIONS,
        allowed_roles=PRODUCT_ROLES,
        output_unit="count",
    ),
    MetricDefinition(
        metric_id="checkout_conversion_rate",
        display_name="Checkout Conversion Rate",
        business_definition="Share of checkout sessions that completed during the reporting period.",
        synonyms=("checkout conversion", "checkout completion rate", "checkout success rate"),
        calculation="Divide the count of sessions with checkout_status = 'completed' by the count of all sessions.",
        source_tables=("checkout_sessions", "customers"),
        data_grain="checkout session",
        date_column="checkout_sessions.session_date",
        allowed_dimensions=CHECKOUT_DIMENSIONS,
        allowed_roles=PRODUCT_ROLES,
        output_unit="ratio",
    ),
    MetricDefinition(
        metric_id="gmv",
        display_name="Gross Merchandise Value",
        business_definition="Total value in AUD of completed orders placed during the reporting period.",
        synonyms=("completed order value", "gross merchandise volume", "total completed order value"),
        calculation="Sum orders.order_amount in cents and divide by 100.",
        source_tables=("orders", "customers"),
        data_grain="order",
        date_column="orders.order_date",
        allowed_dimensions=CUSTOMER_DIMENSIONS,
        allowed_roles=PRODUCT_ROLES,
        output_unit="AUD",
        required_filters=(TEST_ACCOUNT_FILTER, COMPLETED_ORDER_FILTER),
    ),
    MetricDefinition(
        metric_id="average_order_value",
        display_name="Average Order Value",
        business_definition="Mean value in AUD per completed order placed during the reporting period.",
        synonyms=("aov", "average basket value", "average basket size in dollars"),
        calculation="Divide the sum of completed order cents by the completed order count, then divide by 100.",
        source_tables=("orders", "customers"),
        data_grain="order",
        date_column="orders.order_date",
        allowed_dimensions=CUSTOMER_DIMENSIONS,
        allowed_roles=CUSTOMER_ACTIVITY_ROLES,
        output_unit="AUD",
        required_filters=(TEST_ACCOUNT_FILTER, COMPLETED_ORDER_FILTER),
    ),
    MetricDefinition(
        metric_id="payment_volume",
        display_name="Payment Volume",
        business_definition="All payment attempts made during the reporting period, including retries and failures.",
        synonyms=("payment attempts", "payment attempt count", "number of payments"),
        calculation="Count payment attempt rows across both payment statuses.",
        source_tables=("payments", "orders", "customers"),
        data_grain="payment attempt",
        date_column="payments.payment_date",
        allowed_dimensions=PAYMENT_DIMENSIONS,
        allowed_roles=PAYMENTS_ROLES,
        output_unit="count",
    ),
    MetricDefinition(
        metric_id="payment_failure_rate",
        display_name="Payment Failure Rate",
        business_definition="Share of payment attempts made during the reporting period that failed.",
        synonyms=("failed payment rate", "payment decline rate", "payment failure percentage"),
        calculation="Divide the count of attempts with payment_status = 'failed' by the count of all attempts.",
        source_tables=("payments", "orders", "customers"),
        data_grain="payment attempt",
        date_column="payments.payment_date",
        allowed_dimensions=PAYMENT_DIMENSIONS,
        allowed_roles=PAYMENTS_ROLES,
        output_unit="ratio",
    ),
)

METRIC_REGISTRY = MappingProxyType({metric.metric_id: metric for metric in _METRICS})
