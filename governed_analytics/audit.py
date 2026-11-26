"""Local append-only interface for terminal audit events."""

import json
import os
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_AUDIT_PATH = Path("logs/audit.jsonl")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    request_id: str
    timestamp_utc: str
    trusted_role: str
    metric_id: str | None
    dimension: str | None
    start_date: str | None
    end_date: str | None
    authorization_decision: str
    response_status: str
    diagnostic: str | None
    sql_attempted: bool
    sql_succeeded: bool
    latency_ms: float
    returned_row_count: int
    policy_version: str


def append_event(path: Path, event: AuditEvent) -> None:
    # One append write keeps concurrent requests from interleaving their JSON.
    data = (json.dumps(asdict(event), separators=(",", ":")) + "\n").encode("utf-8")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("Audit destination must be a regular file")
        if os.write(fd, data) != len(data):
            raise OSError("Incomplete audit write")
    finally:
        os.close(fd)
