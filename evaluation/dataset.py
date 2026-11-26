"""Load the version-controlled synthetic evaluation cases."""

import json
from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

DATASET_PATH = Path(__file__).with_name("golden.json")


class ExpectedResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    group: str | None
    value: str | None


class ExpectedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["answered", "denied", "unsupported", "clarification_required", "rejected"]
    metric_id: str | None
    dimension: str | None
    start_date: date | None
    end_date: date | None
    authorization: Literal["allowed", "denied", "not_evaluated"] | None
    sql_permitted: bool
    unit: Literal["count", "ratio", "AUD"] | None
    results: list[ExpectedResult] | None


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    case_id: str
    category: Literal["standard", "grouped", "unauthorized", "adversarial", "unsupported", "ambiguous", "edge"]
    origin: Literal["live_baseline", "new"]
    role: Literal["Product", "Marketing", "Payments", "Admin"]
    question: str
    expected: ExpectedOutcome
    offline_intent: dict[str, str | None] | None = None

    def simulated_intent(self) -> str:
        if self.offline_intent is not None:
            return json.dumps(self.offline_intent)
        expected = self.expected
        if expected.status == "answered":
            return json.dumps({
                "intent_status": "answerable",
                "metric_id": expected.metric_id,
                "dimension": expected.dimension,
                "start_date": expected.start_date.isoformat(),
                "end_date": expected.end_date.isoformat(),
            })
        status = "clarification_required" if expected.status == "clarification_required" else "unsupported"
        return json.dumps({"intent_status": status})


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str
    seed: int
    reporting_date: date
    cases: list[Case]


def load_dataset(path: Path = DATASET_PATH) -> Dataset:
    dataset = Dataset.model_validate_json(path.read_text())
    ids = [case.case_id for case in dataset.cases]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate golden case IDs")
    return dataset
