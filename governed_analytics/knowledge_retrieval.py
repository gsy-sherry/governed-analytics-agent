"""Lexical discovery of metric definitions visible to a trusted role."""

import re

from governed_analytics.access_policy import can_access_metric
from governed_analytics.metric_registry import METRIC_REGISTRY, MetricDefinition


def normalize(text: str) -> str:
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def metric_phrases(metric: MetricDefinition) -> set[str]:
    return {
        normalize(phrase)
        for phrase in (metric.metric_id, metric.display_name, *metric.synonyms, metric.business_definition)
    }


def match_metrics(question: str) -> tuple[MetricDefinition, ...]:
    """Detect explicit mentions for policy checks without exposing hidden definitions."""
    query = f" {normalize(question)} "
    matches = []
    for metric in METRIC_REGISTRY.values():
        for phrase in metric_phrases(metric):
            for match in re.finditer(rf"(?<= ){re.escape(phrase)}(?= )", query):
                matches.append((match.start(), match.end(), len(phrase.split()), metric))
    # A specific phrase takes precedence over an overlapping shorter synonym.
    matches.sort(key=lambda match: (-match[2], match[0] - match[1], match[0], match[3].metric_id))
    spans = []
    ranked = {}
    for start, end, words, metric in matches:
        if any(start < other_end and other_start < end for other_start, other_end in spans):
            continue
        spans.append((start, end))
        ranked.setdefault(metric.metric_id, (words, metric))
    return tuple(metric for _, metric in sorted(ranked.values(), key=lambda item: (-item[0], item[1].metric_id)))


def retrieve_metrics(question: str, role: str, *, top_k: int = 3) -> tuple[MetricDefinition, ...]:
    """Rank whole-phrase matches and expose only role-visible definitions."""
    if top_k < 0:
        raise ValueError("top_k must be non-negative")
    return tuple(
        metric for metric in match_metrics(question) if can_access_metric(role, metric.metric_id)
    )[:top_k]
