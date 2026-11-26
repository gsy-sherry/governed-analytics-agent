import json
import sqlite3
from collections import Counter
from contextlib import closing
from dataclasses import replace
from decimal import Decimal

import pytest

from evaluation.__main__ import evaluate, main
from evaluation.dataset import load_dataset
from evaluation.reference import ReferenceData
from evaluation.scoring import load_audit_events, score_case, summarize
from governed_analytics.access_policy import POLICY_VERSION, can_access_metric
from governed_analytics.agent import AnalyticsResponse, Result
from governed_analytics.metric_registry import METRIC_REGISTRY
from governed_analytics.synthetic_data import DEFAULT_SEED, REPORTING_DATE, SCHEMA, generate_database


@pytest.fixture(scope="module")
def dataset():
    return load_dataset()


def test_golden_dataset_validity(dataset):
    assert len(dataset.cases) == 40
    assert dataset.seed == DEFAULT_SEED
    assert dataset.reporting_date == REPORTING_DATE
    assert Counter(case.category for case in dataset.cases) == {
        "standard": 12, "grouped": 6, "unauthorized": 6, "adversarial": 6,
        "unsupported": 4, "ambiguous": 3, "edge": 3,
    }
    assert {case.role for case in dataset.cases} == {"Product", "Marketing", "Payments", "Admin"}
    assert {case.case_id for case in dataset.cases if case.origin == "live_baseline"} == {
        "STD-08", "GRP-01", "AUTH-01", "ADV-01", "UNS-01",
    }
    answerable = [case for case in dataset.cases if case.expected.status == "answered"]
    assert {case.expected.metric_id for case in answerable} == METRIC_REGISTRY.keys()
    assert len({(case.role, case.question) for case in dataset.cases}) == 40
    for case in dataset.cases:
        expected = case.expected
        assert case.question.strip()
        assert expected.sql_permitted == (expected.status == "answered")
        assert (expected.results is not None) == expected.sql_permitted
        if expected.sql_permitted:
            assert expected.start_date < expected.end_date
            assert expected.unit == METRIC_REGISTRY[expected.metric_id].output_unit
            dimensions = (expected.dimension,) if expected.dimension else ()
            assert can_access_metric(case.role, expected.metric_id, dimensions=dimensions)
        if expected.status == "denied":
            assert not can_access_metric(case.role, expected.metric_id)


def test_all_golden_results_are_reproducible(tmp_path, dataset):
    path = tmp_path / "fixture.sqlite3"
    generate_database(path, dataset.seed)
    reference = ReferenceData(path)
    for case in dataset.cases:
        if case.expected.sql_permitted:
            assert reference.calculate(case.expected) == case.expected.results, case.case_id


def test_reference_calculations_on_hand_computed_fixture(tmp_path, dataset):
    path = tmp_path / "small.sqlite3"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(SCHEMA)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript("""
            INSERT INTO customers VALUES
                (1, '2025-06-01', 'AU', 'premium', 'inactive', 0),
                (2, '2025-06-01', 'AU', 'standard', 'active', 0),
                (3, '2025-06-01', 'AU', 'premium', 'active', 1);
            INSERT INTO checkout_sessions VALUES
                (1, 1, '2025-06-01', 'completed', 'Web'),
                (2, 1, '2025-06-02', 'completed', 'Web'),
                (3, 2, '2025-06-02', 'completed', 'iOS'),
                (4, 2, '2025-06-02', 'abandoned', 'iOS'),
                (5, 3, '2025-06-01', 'completed', 'Web');
            INSERT INTO orders VALUES
                (1, 1, 1, '2025-06-01', 1001, 'completed'),
                (2, 1, 2, '2025-06-02', 2002, 'completed'),
                (3, 2, 3, '2025-06-02', 5000, 'refunded'),
                (4, 3, 5, '2025-06-01', 9999, 'completed');
            INSERT INTO payments VALUES
                (1, 1, '2025-06-01', 1001, 'failed', 'card'),
                (2, 1, '2025-06-02', 1001, 'successful', 'card'),
                (3, 3, '2025-06-02', 5000, 'successful', 'card'),
                (4, 2, '2025-06-03', 2002, 'successful', 'card'),
                (5, 4, '2025-06-01', 9999, 'successful', 'card');
        """)
    reference = ReferenceData(path)
    targets = {
        "new_customers": "2", "active_customers": "1", "checkout_volume": "4",
        "checkout_conversion_rate": "0.75", "gmv": "30.03", "average_order_value": "15.015",
        "payment_volume": "3", "payment_failure_rate": "0.33333333333333333333333333333333",
    }
    specs = {case.expected.metric_id: case.expected for case in dataset.cases if case.category == "standard"}
    start, end = REPORTING_DATE.replace(day=1), REPORTING_DATE.replace(day=3)
    for metric, value in targets.items():
        spec = specs[metric].model_copy(update={"start_date": start, "end_date": end})
        result, = reference.calculate(spec)
        assert result.group is None
        assert result.value == value


def _case(dataset, case_id):
    return next(case for case in dataset.cases if case.case_id == case_id)


def _response_and_evidence(case, *, status=None, attempted=None):
    expected = case.expected
    status = status or expected.status
    answered = status == "answered"
    results = tuple(
        Result(result.group, None if result.value is None else Decimal(result.value))
        for result in reversed(expected.results or ())
    ) if answered else ()
    response = AnalyticsResponse(
        status=status, answer="Synthetic answer", request_id=case.case_id,
        metric_id=expected.metric_id if answered else None,
        dimension=expected.dimension if answered else None,
        unit=expected.unit if answered else None,
        start_date=expected.start_date.isoformat() if answered else None,
        end_date=expected.end_date.isoformat() if answered else None,
        results=results,
    )
    attempted = expected.sql_permitted if attempted is None else attempted
    event = {
        "request_id": case.case_id, "trusted_role": case.role, "policy_version": POLICY_VERSION,
        "response_status": status, "returned_row_count": len(results),
        "authorization_decision": expected.authorization or "not_evaluated",
        "sql_attempted": attempted, "sql_succeeded": attempted and answered,
        "metric_id": expected.metric_id,
        "dimension": response.dimension, "start_date": response.start_date,
        "end_date": response.end_date,
    }
    return response, [event]


def test_scoring_denominators_and_failure_categorization(dataset):
    passed = _case(dataset, "GRP-01")
    rejected = _case(dataset, "STD-01")
    denied = _case(dataset, "AUTH-01")
    unsupported = _case(dataset, "UNS-01")
    scored = [
        score_case(passed, *_response_and_evidence(passed), 10),
        score_case(rejected, *_response_and_evidence(rejected, status="unsupported", attempted=False), 20),
        score_case(denied, *_response_and_evidence(denied, attempted=True), 30),
        score_case(unsupported, *_response_and_evidence(unsupported, status="error"), 40),
    ]
    summary = summarize(scored)
    wanted = {
        "status_accuracy": (2, 4),
        "intent_accuracy": (1, 2),
        "numerical_accuracy": (1, 2),
        "authorization_accuracy": (3, 3),
        "unsafe_execution_rate": (1, 2),
        "false_rejection_rate": (1, 2),
        "unsafe_blocking_recall": (1, 2),
        "api_or_system_error_rate": (1, 4),
    }
    assert {
        key: (value["numerator"], value["denominator"])
        for key, value in summary["rates"].items()
    } == wanted
    assert summary["latency_ms"] == {"samples": 4, "p50": 25.0, "p95": 38.5}
    assert summary["failed_case_ids"] == [rejected.case_id, denied.case_id, unsupported.case_id]
    assert "unsafe_sql_attempt" in scored[2]["failures"]
    assert "api_or_system_error" in scored[3]["failures"]
    assert all(rate["value"] is None for rate in summarize([])["rates"].values())


def test_missing_or_invalid_audit_evidence_is_not_a_verified_block(dataset, tmp_path):
    case = _case(dataset, "AUTH-01")
    response, events = _response_and_evidence(case)
    path = tmp_path / "audit.jsonl"
    path.write_text("corrupt log")
    missing = score_case(case, response, load_audit_events(path).get(response.request_id, []), 10)
    duplicate = score_case(case, response, events * 2, 20)
    wrong_role = score_case(case, response, [{**events[0], "trusted_role": "Admin"}], 30)
    assert missing["failures"] == ["authorization_mismatch", "audit_evidence_missing"]
    assert "audit_evidence_invalid" in duplicate["failures"]
    assert "audit_evidence_invalid" in wrong_role["failures"]
    rates = summarize([missing, duplicate, wrong_role])["rates"]
    assert rates["unsafe_execution_rate"] == {
        "numerator": 0, "denominator": 3, "unknown": 3, "value": None,
    }
    assert rates["unsafe_blocking_recall"]["numerator"] == 0


def test_correct_intent_does_not_hide_numerical_error(dataset):
    case = _case(dataset, "GRP-01")
    response, events = _response_and_evidence(case)
    first, second = response.results
    wrong = replace(response, results=(replace(first, value=first.value + Decimal("0.01")), second))
    score = score_case(case, wrong, events, 1)
    assert score["intent_correct"] is True
    assert score["numerical_correct"] is False
    assert score["failures"] == ["numeric_answer_mismatch"]


def test_offline_runner_uses_real_agent_and_writes_audited_reports(dataset, tmp_path, monkeypatch, capsys):
    def no_live_client():
        pytest.fail("Offline evaluation must not configure the live client")

    monkeypatch.setattr("evaluation.__main__.OpenAIInterpreter.from_environment", no_live_client)
    monkeypatch.setattr("sys.argv", [
        "evaluation", "--mode", "offline", "--case", "GRP-01", "--case", "AUTH-01",
        "--output-dir", str(tmp_path),
    ])
    assert main() == 0
    run_dir, = tmp_path.iterdir()
    report = json.loads((run_dir / "report.json").read_text())
    assert report["mode"] == "offline"
    assert report["model"] is None
    assert report["overall"]["case_count"] == 2
    assert report["overall"]["failed_case_ids"] == []
    assert set(report["categories"]) == {"grouped", "unauthorized"}
    evidence = load_audit_events(run_dir / "audit.jsonl")
    assert set(evidence) == {case["request_id"] for case in report["cases"]}
    assert all(case["actual"]["audit_evidence"] == "verified" for case in report["cases"])
    assert "Intent responses were injected" in (run_dir / "summary.txt").read_text()
    assert "OFFLINE evaluation" in capsys.readouterr().out


def test_full_offline_eval_reports_failures_without_changing_expectations(dataset, tmp_path):
    report = evaluate(dataset, dataset.cases, tmp_path / "audit.jsonl")
    assert report["overall"]["case_count"] == 40
    assert all(case["actual"]["audit_evidence"] == "verified" for case in report["cases"])
    rates = report["overall"]["rates"]
    assert rates["unsafe_execution_rate"]["denominator"] == 21
    assert rates["numerical_accuracy"]["denominator"] == 19
    for case in report["cases"]:
        assert case["expected"] == _case(dataset, case["case_id"]).expected.model_dump(mode="json")
