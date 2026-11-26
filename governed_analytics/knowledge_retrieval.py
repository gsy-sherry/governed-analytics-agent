"""Lexical discovery of metric definitions visible to a trusted role."""

import re

from governed_analytics.access_policy import can_access_metric
from governed_analytics.metric_registry import METRIC_REGISTRY, MetricDefinition


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def retrieve_metrics(question: str, role: str, *, top_k: int = 3) -> tuple[MetricDefinition, ...]:
    """Rank whole-phrase matches. Returned definitions are discovery context."""
    if top_k < 0:
        raise ValueError("top_k must be non-negative")
    query = f" {_normalize(question)} "
    candidates = []
    for metric in METRIC_REGISTRY.values():
        if not can_access_metric(role, metric.metric_id):
            continue
        phrases = (metric.metric_id, metric.display_name, *metric.synonyms, metric.business_definition)
        matches = {_normalize(phrase) for phrase in phrases}
        score = max((len(phrase.split()) for phrase in matches if f" {phrase} " in query), default=0)
        if score:
            candidates.append((score, metric))
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1].metric_id))
    return tuple(metric for _, metric in candidates[:top_k])
