"""JSONL telemetry logger for VisionCare."""

from __future__ import annotations

import hashlib
import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock


AUDIT_LOG_PATH = Path(__file__).resolve().parents[1] / "data" / "audit_logs.jsonl"
SUPPORTED_EVENT_TYPES = {"tool_call", "session_summary"}
BASE_REQUIRED_FIELDS = ("schema_version", "session_id", "event_type")
SESSION_SUMMARY_REQUIRED_FIELDS = (
    "intent",
    "anonymous_user_id",
    "issue_category",
    "hardware_issue_detected",
    "sentiment",
    "resolved",
    "escalated",
    "rma_id",
    "model",
    "latency_ms",
    "resolution_time_sec",
)
DISALLOWED_TELEMETRY_FIELDS = {
    "address",
    "customer_email",
    "customer_name",
    "email",
    "home_address",
    "name",
    "phone",
    "phone_number",
    "user_id",
}


def append_event(
    event: dict[str, Any], log_path: str | Path | None = None
) -> dict[str, Any]:
    """Validate and append one telemetry event to a JSONL audit log."""
    validated_event = _validate_event(event)
    resolved_path = _resolve_log_path(log_path)
    resolved_path.parent.mkdir(parents=True, exist_ok=True)

    with FileLock(f"{resolved_path}.lock"):
        with resolved_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(validated_event, sort_keys=True))
            handle.write("\n")

    return validated_event


def make_anonymous_user_id(source_identifier: str, salt: str = "visioncare-demo") -> str:
    """Create a stable opaque user identifier for telemetry counts."""
    normalized = _normalize_required("source_identifier", source_identifier).lower()
    digest = hashlib.sha256(f"{salt}:{normalized}".encode("utf-8")).hexdigest()
    return f"anon_{digest[:16]}"


def _resolve_log_path(log_path: str | Path | None) -> Path:
    return Path(log_path) if log_path is not None else AUDIT_LOG_PATH


def _validate_event(event: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise ValueError("event must be a dictionary")

    _reject_disallowed_fields(event)
    validated = deepcopy(event)

    for field_name in BASE_REQUIRED_FIELDS:
        _normalize_required(field_name, validated.get(field_name))

    event_type = str(validated["event_type"]).strip()
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise ValueError(f"unsupported event_type: {event_type}")
    validated["event_type"] = event_type

    if "event_id" not in validated or not str(validated["event_id"]).strip():
        validated["event_id"] = f"evt_{uuid.uuid4().hex}"
    if "timestamp" not in validated or not str(validated["timestamp"]).strip():
        validated["timestamp"] = _utc_timestamp()

    validated["schema_version"] = _validate_schema_version(
        validated["schema_version"]
    )
    validated["session_id"] = _normalize_required(
        "session_id", validated["session_id"]
    )

    if event_type == "session_summary":
        _validate_session_summary(validated)

    return validated


def _validate_session_summary(event: dict[str, Any]) -> None:
    missing = [
        field_name
        for field_name in SESSION_SUMMARY_REQUIRED_FIELDS
        if field_name not in event
    ]
    if missing:
        raise ValueError(f"missing session_summary fields: {', '.join(missing)}")

    for field_name in (
        "intent",
        "anonymous_user_id",
        "issue_category",
        "sentiment",
        "model",
    ):
        event[field_name] = _normalize_required(field_name, event[field_name])

    if event["rma_id"] == "":
        event["rma_id"] = None
    if event["rma_id"] is not None:
        event["rma_id"] = _normalize_required("rma_id", event["rma_id"])

    for field_name in ("hardware_issue_detected", "resolved", "escalated"):
        if not isinstance(event[field_name], bool):
            raise ValueError(f"{field_name} must be a boolean")

    for field_name in ("latency_ms", "resolution_time_sec"):
        if not isinstance(event[field_name], (int, float)):
            raise ValueError(f"{field_name} must be numeric")
        if event[field_name] < 0:
            raise ValueError(f"{field_name} must be non-negative")


def _validate_schema_version(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("schema_version must be an integer")
    if value < 1:
        raise ValueError("schema_version must be positive")
    return value


def _reject_disallowed_fields(event: dict[str, Any]) -> None:
    disallowed = sorted(
        key for key in event if key.lower() in DISALLOWED_TELEMETRY_FIELDS
    )
    if disallowed:
        raise ValueError(
            "telemetry event contains personal identifier fields: "
            + ", ".join(disallowed)
        )


def _normalize_required(field_name: str, value: Any) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
