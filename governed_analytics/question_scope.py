"""Ground a single aggregate request in explicitly recognized question clauses."""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from governed_analytics.knowledge_retrieval import metric_phrases, normalize
from governed_analytics.metric_registry import MetricDefinition
from governed_analytics.synthetic_data import HISTORY_START, REPORTING_DATE

REPORTING_END = REPORTING_DATE + timedelta(days=1)
_DAY = timedelta(days=1)
_MONTHS = {}
for number, name in enumerate("january february march april may june july august september october november december".split(), 1):
    _MONTHS[name] = _MONTHS[name[:3]] = number
_MONTHS["sept"] = 9
_MONTH = "|".join(_MONTHS)
_GROUPS = {
    "customer_segment": r"(?:customer )?segments?",
    "country": r"countr(?:y|ies)",
    "device_type": r"(?:device types?|devices?)",
    "payment_method": r"(?:methods?|payment methods?)",
}
_GROUP_MARKER = r"(?:by|grouped by|broken down by|split by|across)"
_REQUEST = (
    r"(?:please )?(?:(?:could|can|would) you )?"
    r"(?:what (?:is|was|were)|show(?: me)?|give me|calculate|report|tell me|"
    r"how many|break down|split out|i would like(?: to see)?)(?: the)?"
)
_FRAME = re.compile(rf"^(?:{_REQUEST} |please |the )")
_BRIDGE = re.compile(r"^(?:were there|are there|did we (?:have|see|record))\b\s*")
_VAGUE = re.compile(
    r"(?:recently|lately|(?:in |for )?(?:the )?recent (?:period|weeks|months)|"
    r"(?:in |for )?(?:the )?last few (?:days|weeks|months)|(?:this|last) period)"
)


@dataclass(frozen=True, slots=True)
class QuestionScope:
    issue: str | None = None
    dimension: str | None = None
    start: date | None = None
    end: date | None = None


def _point(text: str, year: int) -> tuple[date, date] | None:
    if re.fullmatch(r"\d{4} \d{2} \d{2}", text):
        start = date(*(int(part) for part in text.split()))
        return start, start + _DAY
    match = re.fullmatch(rf"(?:(\d{{1,2}}) (?:of )?)?({_MONTH})(?: (\d{{1,2}}))?(?: (\d{{4}}))?", text)
    if not match:
        return None
    leading_day, month, trailing_day, explicit_year = match.groups()
    if leading_day and trailing_day:
        return None
    year = int(explicit_year) if explicit_year else year
    month = _MONTHS[month]
    day = leading_day or trailing_day
    start = date(year, month, int(day) if day else 1)
    return start, (start + _DAY if day else _shift_month(start, 1))


def _shift_month(first: date, offset: int) -> date:
    year, month = divmod(first.year * 12 + first.month - 1 + offset, 12)
    return date(year, month + 1, 1)


def _period(text: str) -> tuple[date, date] | None:
    text = re.sub(r"^(?:in|for|on|during|over) ", "", text)
    text = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", text)
    if re.fullmatch(r"(?:the )?(?:full|entire) (?:synthetic |reporting )?(?:period|window)", text):
        return HISTORY_START, REPORTING_END
    if text in {"today", "yesterday"}:
        start = REPORTING_DATE - (_DAY if text == "yesterday" else timedelta())
        return start, start + _DAY
    rolling = re.fullmatch(r"(?:the )?(?:last|past) (\d{1,3}) days?", text)
    if rolling:
        return REPORTING_END - timedelta(days=int(rolling[1])), REPORTING_END
    relative = re.fullmatch(r"(?:the )?(last|this|previous|current) (?:calendar )?(week|month|quarter|year)", text)
    if relative:
        previous = relative[1] in {"last", "previous"}
        unit = relative[2]
        if unit == "week":
            monday = REPORTING_DATE - timedelta(days=REPORTING_DATE.weekday())
            start = monday - timedelta(weeks=int(previous))
            return start, start + timedelta(weeks=1)
        months = {"month": 1, "quarter": 3, "year": 12}[unit]
        first = REPORTING_DATE.replace(month=((REPORTING_DATE.month - 1) // months) * months + 1, day=1)
        start = _shift_month(first, -months if previous else 0)
        return start, _shift_month(start, months)

    compact = re.fullmatch(rf"({_MONTH}) (\d{{1,2}}) to (\d{{1,2}})(?: (\d{{4}}))?", text)
    if compact:
        month, first, last, year = compact.groups()
        start = date(int(year or REPORTING_DATE.year), _MONTHS[month], int(first))
        return start, start.replace(day=int(last)) + _DAY
    range_match = re.fullmatch(r"between (.+?) and (.+)", text) or re.fullmatch(
        r"(?:from )?(.+?) (?:to|through) (.+)", text,
    )
    if range_match:
        first, last = range_match.groups()
        first_year = re.search(r"\b\d{4}\b", first)
        last_year = re.search(r"\b\d{4}\b", last)
        start = _point(first, int(last_year[0]) if last_year else REPORTING_DATE.year)
        end = _point(last, int(first_year[0]) if first_year else REPORTING_DATE.year)
        return (start[0], end[1]) if start and end else None
    return _point(text, REPORTING_DATE.year)


def _split_grouping(clauses: str) -> tuple[str | None, str]:
    for dimension, phrase in _GROUPS.items():
        grouping = rf"{_GROUP_MARKER} (?:the )?(?:{phrase})"
        before = re.fullmatch(rf"{grouping}(?: (.+))?", clauses)
        after = re.fullmatch(rf"(?:(.+?) )?{grouping}", clauses)
        if match := before or after:
            return dimension, match[1] or ""
    return None, clauses


def _clean(question: str) -> str | None:
    # Convert only supported temporal possessives. Other punctuation may carry
    # a filter, SQL or instructions and must not disappear during normalization.
    text = re.sub(r"\b(today|yesterday)['’]s\b", r"\1", question.casefold())
    if re.search(r"--|(?<=\d)[.,](?=\d)|[^\w\s?,.!–—-]", text):
        return None
    text = re.sub(rf"\b({_MONTH}) (\d{{1,2}})-(\d{{1,2}})\b", r"\1 \2 to \3", text)
    text = re.sub(r"\s*[–—]\s*", " to ", text)
    return re.sub(r" please$", "", _FRAME.sub("", normalize(text), count=1)).strip()


def inspect_question(question: str, metric: MetricDefinition | None) -> QuestionScope:
    text = _clean(question)
    if text is None:
        return QuestionScope("unsupported_question_shape")
    if metric is None:
        # Only generic references invite clarification. An explicitly named
        # measure with no approved match is outside the catalog.
        generic = re.fullmatch(
            r"(?:rate|volume|value|metric|measure|performance|number|numbers|results|total|it|how did we do)(?: (.+))?", text,
        )
        try:
            if not text or (generic and (not generic[1] or _period(generic[1]) or _VAGUE.fullmatch(generic[1]))):
                return QuestionScope("ambiguous_intent")
        except (ValueError, OverflowError):
            pass
        return QuestionScope("unmatched_metric")

    phrases = "|".join(re.escape(phrase) for phrase in sorted(metric_phrases(metric), key=lambda p: (-len(p), p)))
    match = re.search(rf"(?<!\w)(?:{phrases})(?!\w)", text)
    if not match:
        return QuestionScope("unsupported_question_shape")
    before = text[:match.start()].strip()
    after = _BRIDGE.sub("", text[match.end():].strip())
    dimension, period = _split_grouping(after)
    if before:
        # A leading period can accompany a metric and optional grouping, but
        # cannot be combined with a second period or any unconsumed tokens.
        if period:
            return QuestionScope("unsupported_question_shape")
        period = re.sub(rf" {_REQUEST}$", "", before)
    if not period or _VAGUE.fullmatch(period):
        return QuestionScope("ambiguous_intent", dimension)
    try:
        dates = _period(period)
    except (ValueError, OverflowError):
        return QuestionScope("invalid_period", dimension)
    if dates is None:
        # Report an unapproved grouping only if the rest is one valid period.
        unknown_group = re.match(rf"^{_GROUP_MARKER} (.+?) ((?:in|on|for|during) .+)$", period)
        if unknown_group:
            try:
                if _period(unknown_group[2]):
                    return QuestionScope("invalid_dimension")
            except (ValueError, OverflowError):
                pass
        return QuestionScope("unsupported_question_shape")
    start, end = dates
    if not HISTORY_START <= start < end <= REPORTING_END:
        return QuestionScope("invalid_period", dimension, start, end)
    return QuestionScope(dimension=dimension, start=start, end=end)
