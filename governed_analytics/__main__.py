"""Local demonstration CLI for the governed analytics agent."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from governed_analytics.access_policy import ROLES
from governed_analytics.agent import AnalyticsAgent
from governed_analytics.intent import OpenAIInterpreter


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=sorted(ROLES.values()), help="Simulated trusted role for local demonstration")
    parser.add_argument("--db", type=Path, default=Path("synthetic.sqlite3"))
    parser.add_argument("question")
    args = parser.parse_args()
    def interpret(question, candidates):
        return OpenAIInterpreter.from_environment()(question, candidates)

    response = AnalyticsAgent(args.db, interpret).ask(args.question, trusted_role=args.role)
    print(json.dumps(asdict(response), indent=2, default=str))
    return 0 if response.status == "answered" else 1


if __name__ == "__main__":
    raise SystemExit(main())
