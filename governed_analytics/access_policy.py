"""Default-deny authorization for registered aggregate metrics."""

from governed_analytics.metric_registry import METRIC_REGISTRY


def can_access_metric(role: str, metric_id: str, *, dimensions: tuple[str, ...] = ()) -> bool:
    """Check a trusted role against an exact metric ID and approved dimensions."""
    metric = METRIC_REGISTRY.get(metric_id)
    if metric is None:
        return False
    normalized_role = role.strip().casefold()
    return (
        any(normalized_role == allowed.casefold() for allowed in metric.allowed_roles)
        and all(dimension in metric.allowed_dimensions for dimension in dimensions)
    )
