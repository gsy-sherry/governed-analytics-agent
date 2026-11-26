"""Governed analytics workflow for one aggregate question."""

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError

from governed_analytics.access_policy import can_access_metric
from governed_analytics.approved_queries import AggregateRow, _ApprovedQuery, _build_sql, _execute_query
from governed_analytics.intent import REPORTING_END, IntentInterpreter, QueryPlan
from governed_analytics.knowledge_retrieval import retrieve_metrics
from governed_analytics.metric_registry import METRIC_REGISTRY, MetricDefinition
from governed_analytics.synthetic_data import HISTORY_START


@dataclass(frozen=True, slots=True)
class Result:
    group: str | None
    value: int | Decimal | None


@dataclass(frozen=True, slots=True)
class AnalyticsResponse:
    status: str
    answer: str
    diagnostic: str | None = None
    metric_id: str | None = None
    metric_definition: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    dimension: str | None = None
    unit: str | None = None
    results: tuple[Result, ...] = ()


@dataclass(frozen=True, slots=True)
class _TrustedContext:
    role: str


class _State(TypedDict):
    question: str
    candidates: NotRequired[tuple[MetricDefinition, ...]]
    raw_plan: NotRequired[str]
    plan: NotRequired[QueryPlan]
    query: NotRequired[_ApprovedQuery]
    rows: NotRequired[list[AggregateRow]]
    response: NotRequired[AnalyticsResponse]


def _stop(status: str, diagnostic: str, message: str) -> dict[str, AnalyticsResponse]:
    return {"response": AnalyticsResponse(status, message, diagnostic)}


def _retrieve(state: _State, runtime: Runtime[_TrustedContext]) -> dict:
    return {"candidates": retrieve_metrics(state["question"], runtime.context.role, top_k=len(METRIC_REGISTRY))}


def _validate(state: _State, runtime: Runtime[_TrustedContext]) -> dict:
    try:
        plan = QueryPlan.model_validate_json(state["raw_plan"])
    except ValidationError:
        return _stop("rejected", "invalid_plan", "The request did not produce a valid query plan.")
    if plan.intent_status == "unsupported":
        return _stop("unsupported", "unsupported_intent", "This request is outside the approved analytics scope.")
    if plan.intent_status == "clarification_required":
        return _stop("clarification_required", "ambiguous_intent", "Please specify one metric and a reporting period.")

    metric = METRIC_REGISTRY.get(plan.metric_id)
    if metric is None:
        return _stop("rejected", "invalid_plan", "The request did not produce a valid query plan.")
    if not can_access_metric(runtime.context.role, metric.metric_id):
        return _stop("denied", "metric_access_denied", "This role cannot access the requested metric.")
    if plan.dimension is not None and not can_access_metric(
        runtime.context.role, metric.metric_id, dimensions=(plan.dimension,),
    ):
        return _stop("rejected", "invalid_dimension", "That grouping is unavailable for the requested metric.")
    if not HISTORY_START <= plan.start_date < plan.end_date <= REPORTING_END:
        return _stop("rejected", "invalid_period", "Choose a non-empty period within January through June 2025.")
    if metric not in state["candidates"]:
        return _stop("unsupported", "unmatched_metric", "The requested metric could not be matched to an approved definition.")
    return {"plan": plan}


def _build(state: _State) -> dict:
    try:
        return {"query": _build_sql(state["plan"])}
    except (KeyError, ValueError):
        return _stop("error", "query_build_failed", "The approved query could not be prepared.")


def _result_value(numerator: int, denominator: int | None, unit: str) -> int | Decimal | None:
    if denominator == 0:
        return None
    if unit == "count":
        return numerator
    value = Decimal(numerator) / (denominator if denominator is not None else 1)
    return value / 100 if unit == "AUD" else value


def _display(value: int | Decimal | None, unit: str) -> str:
    if value is None:
        return "undefined (zero denominator)"
    if unit == "ratio":
        return f"{value:.2%}"
    if unit == "AUD":
        return f"AUD {value:,.2f}"
    return f"{value:,}"


def _format(state: _State) -> dict:
    if "response" in state:
        return {"response": state["response"]}
    plan = state["plan"]
    metric = METRIC_REGISTRY[plan.metric_id]
    results = tuple(
        Result(group, _result_value(numerator, denominator, metric.output_unit))
        for group, numerator, denominator in state["rows"]
    )
    values = "; ".join(
        f"{result.group}: {_display(result.value, metric.output_unit)}"
        if result.group is not None else _display(result.value, metric.output_unit)
        for result in results
    )
    return {"response": AnalyticsResponse(
        status="answered",
        answer=f"{metric.display_name}: {values}" if results else f"{metric.display_name}: no matching groups.",
        metric_id=metric.metric_id,
        metric_definition=metric.business_definition,
        start_date=plan.start_date.isoformat(),
        end_date=plan.end_date.isoformat(),
        dimension=plan.dimension,
        unit=metric.output_unit,
        results=results,
    )}


def _route(state: _State) -> str:
    return "stop" if "response" in state else "continue"


class AnalyticsAgent:
    def __init__(self, db_path: Path, interpreter: IntentInterpreter):
        def interpret(state: _State) -> dict:
            try:
                return {"raw_plan": interpreter(state["question"], state["candidates"])}
            except Exception:
                # SDK and adapter errors can contain request data.
                return _stop("error", "intent_failed", "The intent service could not interpret the question.")

        def execute(state: _State) -> dict:
            try:
                return {"rows": _execute_query(db_path, state["query"])}
            except (sqlite3.Error, OSError, ValueError):
                return _stop("error", "database_failed", "The approved aggregate query could not be completed.")

        graph = StateGraph(_State, context_schema=_TrustedContext)
        graph.add_node("retrieve_context", _retrieve)
        graph.add_node("interpret_intent", interpret)
        graph.add_node("validate_plan", _validate)
        graph.add_node("build_sql", _build)
        graph.add_node("execute_query", execute)
        graph.add_node("format_answer", _format)
        graph.add_edge(START, "retrieve_context")
        graph.add_edge("retrieve_context", "interpret_intent")
        graph.add_conditional_edges("interpret_intent", _route, {"stop": "format_answer", "continue": "validate_plan"})
        graph.add_conditional_edges("validate_plan", _route, {"stop": "format_answer", "continue": "build_sql"})
        graph.add_conditional_edges("build_sql", _route, {"stop": "format_answer", "continue": "execute_query"})
        graph.add_edge("execute_query", "format_answer")
        graph.add_edge("format_answer", END)
        self._graph = graph.compile()

    def ask(self, question: str, *, trusted_role: str) -> AnalyticsResponse:
        return self._graph.invoke({"question": question}, context=_TrustedContext(trusted_role))["response"]
