"""Run the curated Governed Analytics Agent evaluation."""

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from time import perf_counter

from evaluation.dataset import DATASETS, Case, Dataset, load_dataset
from evaluation.scoring import load_audit_events, score_case, summarize
from governed_analytics.access_policy import POLICY_VERSION
from governed_analytics.agent import AnalyticsAgent
from governed_analytics.intent import IntentInterpreter, OpenAIInterpreter
from governed_analytics.synthetic_data import REPORTING_DATE, generate_database


def evaluate(
    dataset: Dataset,
    cases: list[Case],
    audit_path: Path,
    *,
    live_interpreter: IntentInterpreter | None = None,
    model: str | None = None,
) -> dict:
    if dataset.reporting_date != REPORTING_DATE:
        raise ValueError("Dataset and fixture reporting dates differ")
    observed = []
    with TemporaryDirectory() as directory:
        db_path = Path(directory) / "fixture.sqlite3"
        generate_database(db_path, dataset.seed)
        for case in cases:
            if live_interpreter is None:
                simulated = case.simulated_intent()

                def interpreter(question, candidates):
                    return simulated
            else:
                interpreter = live_interpreter
            agent = AnalyticsAgent(db_path, interpreter, audit_path=audit_path)
            started = perf_counter()
            response = agent.ask(case.question, trusted_role=case.role)
            observed.append((case, response, (perf_counter() - started) * 1000))

    audit = load_audit_events(audit_path)
    request_counts = Counter(response.request_id for _, response, _ in observed)
    scored = []
    by_category = defaultdict(list)
    for case, response, latency in observed:
        events = audit.get(response.request_id, [])
        if request_counts[response.request_id] > 1:
            events = events * 2
        result = score_case(case, response, events, latency)
        scored.append(result)
        by_category[case.category].append(result)
    return {
        "mode": "live" if live_interpreter is not None else "offline",
        "model": model,
        "dataset_version": dataset.version,
        "fixture_seed": dataset.seed,
        "reporting_date": dataset.reporting_date.isoformat(),
        "policy_version": POLICY_VERSION,
        "overall": summarize(scored),
        "categories": {category: summarize(items) for category, items in sorted(by_category.items())},
        "cases": scored,
    }


def _summary(report: dict) -> str:
    mode = report["mode"]
    lines = [f"{mode.upper()} evaluation · dataset {report['dataset_version']} · policy {report['policy_version']}"]
    if mode == "offline":
        lines.append("Intent responses were injected. Natural-language interpretation by OpenAI was not evaluated.")
    elif report["model"]:
        lines.append(f"Model: {report['model']}")
    for label, metrics in [("overall", report["overall"]), *report["categories"].items()]:
        lines.append(f"\n{label} ({metrics['case_count']} cases)")
        for name, rate in metrics["rates"].items():
            value = f"{rate['value']:.1%}" if rate["value"] is not None else "n/a"
            unknown = f", {rate['unknown']} unknown" if rate["unknown"] else ""
            lines.append(f"  {name}: {rate['numerator']}/{rate['denominator']} ({value}{unknown})")
        latency = metrics["latency_ms"]
        lines.append(f"  P50 / P95 latency: {latency['p50']} / {latency['p95']} ms ({latency['samples']} samples)")
    failures = [case for case in report["cases"] if case["failures"]]
    lines.append("\nFailed cases:")
    if failures:
        for case in failures:
            actual = case["actual"]
            reason = ", ".join(case["failures"])
            diagnostic = f", diagnostic {actual['diagnostic']}" if actual["diagnostic"] else ""
            lines.append(
                f"  {case['case_id']}: expected {case['expected']['status']}, observed {actual['status']} "
                f"({reason}{diagnostic})"
            )
    else:
        lines.append("  None")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("offline", "live"), default="offline", help="Explicitly select live to call OpenAI")
    parser.add_argument("--dataset", choices=tuple(DATASETS), default="golden")
    parser.add_argument("--case", action="append", help="Select a case ID (repeatable)")
    parser.add_argument("--category", action="append", help="Select a category (repeatable)")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/evaluation"))
    args = parser.parse_args()

    dataset = load_dataset(DATASETS[args.dataset])
    if set(args.case or ()) - {case.case_id for case in dataset.cases}:
        parser.error("Unknown case ID")
    if set(args.category or ()) - {case.category for case in dataset.cases}:
        parser.error("Unknown category")
    cases = [
        case for case in dataset.cases
        if (not args.case or case.case_id in args.case)
        and (not args.category or case.category in args.category)
    ]
    if not cases:
        parser.error("No cases selected")

    model = None
    interpreter = None
    if args.mode == "live":
        model = os.getenv("OPENAI_MODEL", "")
        if (
            not re.fullmatch(r"[\w][\w.:/-]{0,199}", model, re.ASCII)
            or model.startswith("sk-")
            or model == os.getenv("OPENAI_API_KEY")
        ):
            parser.error("Set OPENAI_MODEL to a valid model identifier in the process environment")
        try:
            interpreter = OpenAIInterpreter.from_environment()
        except Exception:
            parser.error("Could not configure OpenAI. Set OPENAI_API_KEY and OPENAI_MODEL in the process environment")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_dir = Path(mkdtemp(prefix=f"{args.mode}-", dir=args.output_dir))
    report = evaluate(dataset, cases, run_dir / "audit.jsonl", live_interpreter=interpreter, model=model)
    summary = _summary(report)
    (run_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    (run_dir / "summary.txt").write_text(summary)
    print(summary, end="")
    print(f"Reports: {run_dir}")
    return int(bool(report["overall"]["failed_case_ids"]))


if __name__ == "__main__":
    raise SystemExit(main())
