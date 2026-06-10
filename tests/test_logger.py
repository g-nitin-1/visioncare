import json

import pytest

from backend import logger


def session_summary_event(**overrides):
    event = {
        "schema_version": 1,
        "session_id": "session-1",
        "event_type": "session_summary",
        "intent": "warranty_check",
        "anonymous_user_id": "anon_123",
        "issue_category": "router_connectivity",
        "hardware_issue_detected": True,
        "sentiment": "neutral",
        "resolved": True,
        "escalated": False,
        "rma_id": None,
        "model": "test/model",
        "latency_ms": 1000,
        "resolution_time_sec": 45,
    }
    event.update(overrides)
    return event


def read_jsonl(log_path):
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_append_two_events_creates_two_jsonl_rows(tmp_path):
    log_path = tmp_path / "audit_logs.jsonl"

    first = logger.append_event(
        {
            "schema_version": 1,
            "session_id": "session-1",
            "event_type": "tool_call",
            "tool_name": "check_warranty_status",
        },
        log_path=log_path,
    )
    second = logger.append_event(
        session_summary_event(session_id="session-1"),
        log_path=log_path,
    )
    rows = read_jsonl(log_path)

    assert len(rows) == 2
    assert rows[0]["event_id"] == first["event_id"]
    assert rows[1]["event_id"] == second["event_id"]
    assert rows[0]["timestamp"].endswith("Z")
    assert rows[1]["timestamp"].endswith("Z")


def test_append_event_does_not_mutate_input(tmp_path):
    log_path = tmp_path / "audit_logs.jsonl"
    event = {
        "schema_version": 1,
        "session_id": "session-1",
        "event_type": "tool_call",
        "metadata": {"tool_name": "check_warranty_status"},
    }

    result = logger.append_event(event, log_path=log_path)

    assert "event_id" not in event
    assert "timestamp" not in event
    assert result["metadata"] == event["metadata"]
    assert result["metadata"] is not event["metadata"]


def test_missing_base_required_field_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="session_id is required"):
        logger.append_event(
            {"schema_version": 1, "event_type": "tool_call"},
            log_path=tmp_path / "audit_logs.jsonl",
        )


def test_missing_session_summary_field_is_rejected(tmp_path):
    event = session_summary_event()
    event.pop("intent")

    with pytest.raises(ValueError, match="missing session_summary fields: intent"):
        logger.append_event(event, log_path=tmp_path / "audit_logs.jsonl")


def test_unsupported_event_type_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unsupported event_type"):
        logger.append_event(
            {
                "schema_version": 1,
                "session_id": "session-1",
                "event_type": "debug",
            },
            log_path=tmp_path / "audit_logs.jsonl",
        )


def test_obvious_personal_identifier_fields_are_rejected(tmp_path):
    with pytest.raises(ValueError, match="personal identifier"):
        logger.append_event(
            {
                "schema_version": 1,
                "session_id": "session-1",
                "event_type": "tool_call",
                "user_id": "customer-123",
            },
            log_path=tmp_path / "audit_logs.jsonl",
        )


def test_make_anonymous_user_id_is_stable_and_opaque():
    first = logger.make_anonymous_user_id("customer-123@example.com")
    second = logger.make_anonymous_user_id("customer-123@example.com")

    assert first == second
    assert first.startswith("anon_")
    assert "customer" not in first
    assert "example" not in first
