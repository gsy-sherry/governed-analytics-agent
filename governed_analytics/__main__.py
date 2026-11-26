"""Local demonstration CLI for the governed analytics agent."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from governed_analytics.agent import AnalyticsAgent, AnalyticsResponse
from governed_analytics.intent import OpenAIInterpreter
from governed_analytics.metric_registry import METRIC_REGISTRY


def main() -> int:
    roles = sorted({role for metric in METRIC_REGISTRY.values() for role in metric.allowed_roles})
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=roles, help="Simulated trusted role for local demonstration")
    parser.add_argument("--db", type=Path, default=Path("synthetic.sqlite3"))
    parser.add_argument("question")
    args = parser.parse_args()
    try:
        interpreter = OpenAIInterpreter.from_environment()
    except ValueError:
        response = AnalyticsResponse(
            "error", "Set OPENAI_API_KEY and OPENAI_MODEL in the local environment.", "openai_configuration"
        )
    else:
        response = AnalyticsAgent(args.db, interpreter).ask(args.question, trusted_role=args.role)
    print(json.dumps(asdict(response), indent=2, default=str))
    return 0 if response.status == "answered" else 1


if __name__ == "__main__":
    raise SystemExit(main())
