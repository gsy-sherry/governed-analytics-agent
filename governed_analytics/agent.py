"""Governed analytics workflow for one aggregate question."""

import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter
from typing import Literal, NotRequired, TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context
from openai import APIConnectionError, APIError, APIResponseValidationError
from pydantic import ValidationError

from governed_analytics.access_policy import POLICY_VERSION, ROLES, can_access_metric
from governed_analytics.approved_queries import AggregateRow, _ApprovedQuery, _build_sql, _execute_query
from governed_analytics.audit import DEFAULT_AUDIT_PATH, AuditEvent, append_event
from governed_analytics.intent import (
    REPORTING_END,
    IncompleteIntentError,
    IntentConfigurationError,
    IntentInterpreter,
    IntentOutput,
    IntentServiceError,
    QueryPlan,
)
from governed_analytics.knowledge_retrieval import match_metrics
from governed_analytics.metric_registry import METRIC_REGISTRY, MetricDefinition
from governed_analytics.question_scope import QuestionScope, inspect_question
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
    request_id: str | None = None


@dataclass(slots=True)
class _TrustedContext:
    role: str
    metric_id: str | None = None
    plan: QueryPlan | None = None
    authorization: Literal["allowed", "denied", "not_evaluated"] = "not_evaluated"
    sql_attempted: bool = False
    sql_succeeded: bool = False


class _State(TypedDict):
    question: str
    candidates: NotRequired[tuple[MetricDefinition, ...]]
    scope: NotRequired[QuestionScope]
    raw_plan: NotRequired[str]
    plan: NotRequired[QueryPlan]
    query: NotRequired[_ApprovedQuery]
    rows: NotRequired[list[AggregateRow]]
    response: NotRequired[AnalyticsResponse]


def _stop(status: str, diagnostic: str, message: str) -> dict[str, AnalyticsResponse]:
    return {"response": AnalyticsResponse(status, message, diagnostic)}


def _clarify() -> dict[str, AnalyticsResponse]:
    return _stop("clarification_required", "ambiguous_intent", "Please specify one metric and a reporting period.")


def _reject_dimension() -> dict[str, AnalyticsResponse]:
    return _stop("rejected", "invalid_dimension", "That grouping is unavailable for the requested metric.")


def _retrieve(state: _State, runtime: Runtime[_TrustedContext]) -> dict:
    context = runtime.context
    question = state["question"]
    if len(question) > 4096:
        return _stop("unsupported", "question_too_long", "Please shorten the analytics question.")
    matches = match_metrics(question)
    for metric in matches:
        if not can_access_metric(context.role, metric.metric_id):
            context.metric_id = metric.metric_id
            context.authorization = "denied"
            return _stop("denied", "metric_access_denied", "This role cannot access the requested metric.")
    if context.role == "Unknown":
        context.authorization = "denied"
        return _stop("denied", "unknown_role", "The trusted role is unavailable.")
    if len(matches) > 1:
        return _stop("unsupported", "multiple_metrics", "Request one approved metric at a time.")
    metric = matches[0] if matches else None
    if metric:
        context.metric_id = metric.metric_id
    scope = inspect_question(question, metric)
    if scope.issue == "invalid_dimension" or (
        scope.dimension is not None and metric is not None
        and not can_access_metric(context.role, metric.metric_id, dimensions=(scope.dimension,))
    ):
        context.authorization = "denied"
        return _reject_dimension()
    if scope.issue == "ambiguous_intent":
        return _clarify()
    if scope.issue == "invalid_period":
        return _stop("rejected", "invalid_period", "Choose a non-empty period within January through June 2025.")
    if scope.issue == "unmatched_metric":
        return _stop("unsupported", "unmatched_metric", "The requested metric is outside the approved catalog.")
    if scope.issue:
        return _stop("unsupported", "unsupported_question_shape", "Specify one approved metric, a period and optionally one grouping.")
    return {"candidates": matches, "scope": scope}


def _validate(state: _State, runtime: Runtime[_TrustedContext]) -> dict:
    raw_plan = state["raw_plan"]
    if not isinstance(raw_plan, str) or len(raw_plan) > 8192:
        return _stop("rejected", "invalid_plan", "The request did not produce a valid query plan.")
    try:
        intent = IntentOutput.model_validate_json(raw_plan)
        if intent.intent_status == "answerable":
            if intent.metric_id is not None and intent.metric_id not in METRIC_REGISTRY:
                return _stop("unsupported", "unapproved_metric", "This metric is outside the approved analytics scope.")
            plan = QueryPlan.model_validate_json(raw_plan)
    except ValidationError:
        return _stop("rejected", "invalid_plan", "The request did not produce a valid query plan.")
    if intent.intent_status == "unsupported":
        return _stop("unsupported", "unsupported_intent", "This request is outside the approved analytics scope.")
    if intent.intent_status == "clarification_required":
        return _clarify()

    context = runtime.context
    metric = METRIC_REGISTRY.get(plan.metric_id)
    if metric is None:
        return _stop("rejected", "invalid_plan", "The request did not produce a valid query plan.")
    if not can_access_metric(context.role, metric.metric_id):
        context.metric_id = metric.metric_id
        context.authorization = "denied"
        return _stop("denied", "metric_access_denied", "This role cannot access the requested metric.")
    if plan.dimension is not None and not can_access_metric(
        context.role, metric.metric_id, dimensions=(plan.dimension,),
    ):
        context.authorization = "denied"
        return _reject_dimension()
    if not HISTORY_START <= plan.start_date < plan.end_date <= REPORTING_END:
        return _stop("rejected", "invalid_period", "Choose a non-empty period within January through June 2025.")
    if metric not in state["candidates"]:
        return _stop("unsupported", "unmatched_metric", "The requested metric could not be matched to an approved definition.")
    scope = state["scope"]
    if plan.dimension != scope.dimension:
        return _stop("rejected", "dimension_mismatch", "The interpreted grouping did not match the request.")
    if (plan.start_date, plan.end_date) != (scope.start, scope.end):
        return _stop("rejected", "period_mismatch", "The interpreted period did not match the request.")
    context.plan = plan
    context.authorization = "allowed"
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
    def __init__(self, db_path: Path, interpreter: IntentInterpreter, *, audit_path: Path | None = None):
        self._audit_path = DEFAULT_AUDIT_PATH if audit_path is None else audit_path

        def interpret(state: _State) -> dict:
            try:
                return {"raw_plan": interpreter(state["question"], state["candidates"])}
            except (ValidationError, APIResponseValidationError):
                return _stop("rejected", "invalid_plan", "The request did not produce a valid query plan.")
            except IncompleteIntentError:
                return _stop("error", "intent_incomplete", "The intent service returned no complete structured response.")
            except IntentConfigurationError:
                return _stop("error", "openai_configuration", "Set OPENAI_API_KEY and OPENAI_MODEL in the local environment.")
            except APIConnectionError:
                return _stop("error", "intent_connection_failed", "The intent service could not be reached.")
            except (APIError, IntentServiceError):
                return _stop("error", "intent_api_failed", "The intent service could not complete the request.")
            except Exception:
                # SDK and adapter errors can contain request data.
                return _stop("error", "intent_failed", "The intent service could not interpret the question.")

        def execute(state: _State, runtime: Runtime[_TrustedContext]) -> dict:
            runtime.context.sql_attempted = True
            try:
                rows = _execute_query(db_path, state["query"])
            except (sqlite3.Error, OSError, ValueError):
                return _stop("error", "database_failed", "The approved aggregate query could not be completed.")
            runtime.context.sql_succeeded = True
            return {"rows": rows}

        graph = StateGraph(_State, context_schema=_TrustedContext)
        graph.add_node("retrieve_context", _retrieve)
        graph.add_node("interpret_intent", interpret)
        graph.add_node("validate_plan", _validate)
        graph.add_node("build_sql", _build)
        graph.add_node("execute_query", execute)
        graph.add_node("format_answer", _format)
        graph.add_edge(START, "retrieve_context")
        graph.add_conditional_edges("retrieve_context", _route, {"stop": "format_answer", "continue": "interpret_intent"})
        graph.add_conditional_edges("interpret_intent", _route, {"stop": "format_answer", "continue": "validate_plan"})
        graph.add_conditional_edges("validate_plan", _route, {"stop": "format_answer", "continue": "build_sql"})
        graph.add_conditional_edges("build_sql", _route, {"stop": "format_answer", "continue": "execute_query"})
        graph.add_edge("execute_query", "format_answer")
        graph.add_edge("format_answer", END)
        self._graph = graph.compile()

    def ask(self, question: str, *, trusted_role: str) -> AnalyticsResponse:
        started = perf_counter()
        request_id = str(uuid4())
        role = trusted_role.strip().casefold() if isinstance(trusted_role, str) else ""
        context = _TrustedContext(ROLES.get(role, "Unknown"))
        try:
            with tracing_context(enabled=False):
                response = self._graph.invoke({"question": question}, context=context)["response"]
        except Exception:
            response = AnalyticsResponse("error", "The analytics request could not be completed.", "workflow_failed")
        plan = context.plan
        try:
            append_event(self._audit_path, AuditEvent(
                request_id=request_id,
                timestamp_utc=datetime.now(timezone.utc).isoformat(),
                trusted_role=context.role,
                metric_id=context.metric_id,
                dimension=plan.dimension if plan else None,
                start_date=plan.start_date.isoformat() if plan else None,
                end_date=plan.end_date.isoformat() if plan else None,
                authorization_decision=context.authorization,
                response_status=response.status,
                diagnostic=response.diagnostic,
                sql_attempted=context.sql_attempted,
                sql_succeeded=context.sql_succeeded,
                latency_ms=round((perf_counter() - started) * 1000, 3),
                returned_row_count=len(response.results),
                policy_version=POLICY_VERSION,
            ))
        except OSError:
            return AnalyticsResponse("error", "The audit event could not be written.", "audit_write_failed", request_id=request_id)
        return replace(response, request_id=request_id)
