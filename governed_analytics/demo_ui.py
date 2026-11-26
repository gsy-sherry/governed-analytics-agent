"""Adapters for presenting governed responses and recorded evaluation evidence."""

import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from governed_analytics.access_policy import POLICY_VERSION
from governed_analytics.agent import AnalyticsAgent, AnalyticsResponse
from governed_analytics.audit import DEFAULT_AUDIT_PATH
from governed_analytics.intent import OpenAIInterpreter

ROOT = Path(__file__).resolve().parents[1]
DEMO_DB = ROOT / "synthetic.sqlite3"
DEMO_AUDIT = ROOT / DEFAULT_AUDIT_PATH
EVALUATION_DIR = ROOT / "evaluation"


@dataclass(frozen=True)
class ExecutionEvidence:
    request_id: str
    authorization: str
    sql_attempted: bool
    sql_succeeded: bool
    policy_version: str


@dataclass(frozen=True)
class DemoRun:
    role: str
    response: AnalyticsResponse
    latency_ms: float
    evidence: ExecutionEvidence | None


def _execution_evidence(response: AnalyticsResponse, role: str) -> ExecutionEvidence | None:
    if not response.request_id:
        return None
    matched_event = None
    try:
        with DEMO_AUDIT.open(encoding="utf-8") as source:
            for line in source:
                event = json.loads(line)
                if not isinstance(event, dict):
                    return None
                if event.get("request_id") != response.request_id:
                    continue
                if matched_event is not None:
                    return None
                matched_event = event
    except (OSError, ValueError):
        return None
    if matched_event is None:
        return None
    event = matched_event
    attempted = event.get("sql_attempted")
    succeeded = event.get("sql_succeeded")
    authorization = event.get("authorization_decision")
    if (
        event.get("trusted_role") != role
        or event.get("response_status") != response.status
        or event.get("diagnostic") != response.diagnostic
        or event.get("returned_row_count") != len(response.results)
        or event.get("policy_version") != POLICY_VERSION
        or authorization not in ("allowed", "denied", "not_evaluated")
        or type(attempted) is not bool
        or type(succeeded) is not bool
        or (succeeded and not attempted)
    ):
        return None
    return ExecutionEvidence(response.request_id, authorization, attempted, succeeded, event["policy_version"])


def run_analysis(question: str, role: str) -> DemoRun:
    def interpret(question, candidates):
        # Denied and other preflight requests never initialise the API client.
        return OpenAIInterpreter.from_environment()(question, candidates)

    agent = AnalyticsAgent(DEMO_DB, interpret, audit_path=DEMO_AUDIT)
    started = perf_counter()
    response = agent.ask(question, trusted_role=role)
    latency_ms = (perf_counter() - started) * 1000
    return DemoRun(role, response, latency_ms, _execution_evidence(response, role))


def _overall_table(report_name: str, section: str) -> dict[str, str]:
    in_section = False
    headings = None
    for line in (EVALUATION_DIR / report_name).read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            in_section = line == f"## {section}"
            headings = None
        if not in_section:
            continue
        if line.startswith("| Scope |"):
            headings = [part.strip() for part in line.strip("|").split("|")]
        elif headings and line.startswith("| Overall |"):
            values = [part.strip() for part in line.strip("|").split("|")]
            return dict(zip(headings, values, strict=True))
    raise ValueError("Overall results were not found in the curated evaluation report")


def live_scores() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Read the original and optimised live summaries from tracked Markdown."""
    return (
        _overall_table("BASELINE.md", "Live run"),
        _overall_table("POST_OPTIMISATION.md", "Original golden dataset"),
        _overall_table("POST_OPTIMISATION.md", "Supplemental validation"),
    )


def verification_record() -> dict:
    return json.loads((EVALUATION_DIR / "UI_VERIFICATION.json").read_text(encoding="utf-8"))
