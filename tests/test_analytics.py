import pytest

from backend import analytics, logger


def session_summary_event(**overrides):
    event = {
        "schema_version": 1,
        "session_id": "session-1",
        "event_type": "session_summary",
        "intent": "warranty_check",
        "anonymous_user_id": "anon_user_1",
        "issue_category": "router_connectivity",
        "hardware_issue_detected": True,
        "sentiment": "neutral",
        "resolved": True,
        "escalated": False,
        "rma_id": None,
        "model": "test/model",
        "latency_ms": 1000,
        "resolution_time_sec": 30,
    }
    event.update(overrides)
    return event


def test_empty_audit_log_returns_zero_metrics(tmp_path):
    log_path = tmp_path / "audit_logs.jsonl"
    log_path.write_text("", encoding="utf-8")

    assert analytics.calculate_metrics(log_path) == {
        "total_completed_sessions": 0,
        "active_users": 0,
        "resolution_rate": 0.0,
        "average_handling_time_sec": 0.0,
        "issue_category_counts": {},
        "sentiment_counts": {},
        "human_escalation_count": 0,
        "warranty_claim_count": 0,
        "open_rma_count": 0,
    }


def test_tool_call_rows_do_not_increase_session_count(tmp_path):
    log_path = tmp_path / "audit_logs.jsonl"
    logger.append_event(
        {
            "schema_version": 1,
            "session_id": "session-1",
            "event_type": "tool_call",
            "tool_name": "check_warranty_status",
        },
        log_path=log_path,
    )

    metrics = analytics.calculate_metrics(log_path)

    assert metrics["total_completed_sessions"] == 0
    assert metrics["active_users"] == 0


def test_session_summaries_produce_dashboard_metrics(tmp_path):
    log_path = tmp_path / "audit_logs.jsonl"
    logger.append_event(
        {
            "schema_version": 1,
            "session_id": "session-tool",
            "event_type": "tool_call",
            "tool_name": "initiate_rma_process",
        },
        log_path=log_path,
    )
    logger.append_event(
        session_summary_event(
            session_id="session-1",
            anonymous_user_id="anon_same_user",
            resolved=True,
            escalated=False,
            rma_id="RMA-0001",
            resolution_time_sec=20,
            sentiment="neutral",
            issue_category="router_connectivity",
            intent="warranty_check",
        ),
        log_path=log_path,
    )
    logger.append_event(
        session_summary_event(
            session_id="session-2",
            anonymous_user_id="anon_same_user",
            resolved=False,
            escalated=True,
            rma_id="RMA-0001",
            resolution_time_sec=40,
            sentiment="frustrated",
            issue_category="unknown_error",
            intent="troubleshooting",
        ),
        log_path=log_path,
    )

    metrics = analytics.calculate_metrics(log_path)

    assert metrics["total_completed_sessions"] == 2
    assert metrics["active_users"] == 1
    assert metrics["resolution_rate"] == pytest.approx(0.5)
    assert metrics["average_handling_time_sec"] == pytest.approx(30.0)
    assert metrics["human_escalation_count"] == 1
    assert metrics["warranty_claim_count"] == 1
    assert metrics["open_rma_count"] == 1
    assert metrics["issue_category_counts"] == {
        "router_connectivity": 1,
        "unknown_error": 1,
    }
    assert metrics["sentiment_counts"] == {
        "frustrated": 1,
        "neutral": 1,
    }


def test_time_window_filters_session_summaries(tmp_path):
    log_path = tmp_path / "audit_logs.jsonl"
    logger.append_event(
        session_summary_event(
            session_id="old-session",
            timestamp="2026-06-08T09:00:00Z",
            anonymous_user_id="anon_old",
        ),
        log_path=log_path,
    )
    logger.append_event(
        session_summary_event(
            session_id="new-session",
            timestamp="2026-06-08T10:00:00Z",
            anonymous_user_id="anon_new",
        ),
        log_path=log_path,
    )

    metrics = analytics.calculate_metrics(
        log_path,
        start_time="2026-06-08T09:30:00Z",
        end_time="2026-06-08T10:30:00Z",
    )

    assert metrics["total_completed_sessions"] == 1
    assert metrics["active_users"] == 1
