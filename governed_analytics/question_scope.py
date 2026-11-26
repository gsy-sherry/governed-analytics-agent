"""Conservative execution gate for the local one-metric question format."""

import re

from governed_analytics.knowledge_retrieval import metric_phrases, normalize
from governed_analytics.metric_registry import MetricDefinition

_GROUPS = {
    "customer_segment": r"(?:customer )?segments?",
    "country": r"countr(?:y|ies)",
    "device_type": r"(?:device|device type)s?",
    "payment_method": r"(?:method|payment method)s?",
}

_MONTH = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_DAY = r"[0-9]{1,2}(?:st|nd|rd|th)?"
_YEAR = r"[0-9]{4}"
_DATE = rf"(?:{_YEAR} [0-9]{{2}} [0-9]{{2}}|{_MONTH}(?: {_DAY})?(?: {_YEAR})?|{_DAY} {_MONTH}(?: {_YEAR})?)"
_RELATIVE = r"(?:(?:last|this|previous) (?:calendar )?(?:week|month|quarter|year)|last [0-9]{1,3} days|today|yesterday|(?:the )?(?:full|entire) (?:synthetic |reporting )?period)"
_PERIOD = (
    rf"(?:between {_DATE} and {_DATE}|from {_DATE} (?:to|through|until) {_DATE}|"
    rf"(?:(?:in|for|on|during) )?(?:{_DATE}(?: (?:to|through|until) {_DATE})?|"
    rf"{_MONTH} {_DAY} {_DAY}(?: {_YEAR})?|{_RELATIVE}))"
)
_PREFIX = r"(?:(?:please )?(?:what (?:is|was|were)|show(?: me)?|give me|calculate|report|tell me|how many)(?: the)? |(?:please |the ))?"


def supports_question(question: str, metric: MetricDefinition, dimension: str | None) -> bool:
    """Require a recognizable metric, period and exactly the planned grouping."""
    # Preserve meaningful punctuation as a rejection signal before token normalization.
    if re.search(r"--|[^\w\s?,.!–—-]", question):
        return False
    names = "|".join(re.escape(phrase) for phrase in sorted(metric_phrases(metric)))
    grouping = rf"(?:by|grouped by|broken down by) {_GROUPS[dimension]}" if dimension else None
    suffix = rf"(?:{grouping} {_PERIOD}|{_PERIOD} {grouping})" if grouping else _PERIOD
    return re.fullmatch(rf"{_PREFIX}(?:{names}) {suffix}(?: please)?", normalize(question)) is not None
