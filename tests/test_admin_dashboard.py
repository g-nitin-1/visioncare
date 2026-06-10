from datetime import datetime, timedelta, timezone
import shutil

import pytest
from streamlit.testing.v1 import AppTest

from backend import agent, logger, model_gateway, tools
from frontend import admin_dashboard, user_view


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


def metric_values(app):
    return {metric.label: metric.value for metric in app.metric}


def button_by_label(app, label):
    return next(button for button in app.button if button.label == label)


def selectbox_by_label(app, label):
    return next(item for item in app.selectbox if item.label == label)


def test_time_window_presets_use_utc_boundaries():
    now = datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)

    assert admin_dashboard.resolve_time_window("All time", now=now) == (
        None,
        None,
    )
    assert admin_dashboard.resolve_time_window(
        "Last 24 hours", now=now
    ) == (now - timedelta(hours=24), now)
    assert admin_dashboard.resolve_time_window(
        "Last 7 days", now=now
    ) == (now - timedelta(days=7), now)


def test_unknown_time_window_is_rejected():
    with pytest.raises(ValueError, match="unsupported dashboard window"):
        admin_dashboard.resolve_time_window("Last century")


def test_distribution_rows_are_sorted_and_humanized():
    assert admin_dashboard.distribution_rows(
        {"router_connectivity": 2, "unknown_error": 1, "neutral": 2}
    ) == [
        {"Category": "Neutral", "Count": 2},
        {"Category": "Router Connectivity", "Count": 2},
        {"Category": "Unknown Error", "Count": 1},
    ]


def test_empty_dashboard_renders_zero_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    log_path = tmp_path / "audit_logs.jsonl"
    log_path.write_text("", encoding="utf-8")

    app = AppTest.from_file("app.py", default_timeout=10).run()
    app.session_state[
        admin_dashboard.DASHBOARD_LOG_PATH_STATE_KEY
    ] = str(log_path)
    app.run(timeout=10)
    metrics = metric_values(app)

    assert not app.exception
    assert metrics == {
        "Total sessions": "0",
        "Active users": "0",
        "Resolution rate": "0.0%",
        "Average handling time": "0.0 sec",
        "Warranty claims": "0",
        "Open RMAs": "0",
        "Human escalations": "0",
    }
    assert any(
        "No completed support sessions" in item.value for item in app.info
    )


def test_dashboard_displays_session_summary_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    log_path = tmp_path / "audit_logs.jsonl"
    logger.append_event(
        {
            "schema_version": 1,
            "session_id": "tool-only",
            "event_type": "tool_call",
            "tool_name": "check_warranty_status",
        },
        log_path=log_path,
    )
    logger.append_event(
        session_summary_event(
            session_id="resolved-session",
            anonymous_user_id="anon_same",
            rma_id="RMA-0001",
            resolution_time_sec=20,
        ),
        log_path=log_path,
    )
    logger.append_event(
        session_summary_event(
            session_id="escalated-session",
            anonymous_user_id="anon_same",
            intent="human_escalation",
            issue_category="unknown_error",
            sentiment="frustrated",
            resolved=False,
            escalated=True,
            resolution_time_sec=40,
        ),
        log_path=log_path,
    )

    app = AppTest.from_file("app.py", default_timeout=10).run()
    app.session_state[
        admin_dashboard.DASHBOARD_LOG_PATH_STATE_KEY
    ] = str(log_path)
    app.run(timeout=10)
    metrics = metric_values(app)

    assert not app.exception
    assert metrics["Total sessions"] == "2"
    assert metrics["Active users"] == "1"
    assert metrics["Resolution rate"] == "50.0%"
    assert metrics["Average handling time"] == "30.0 sec"
    assert metrics["Warranty claims"] == "1"
    assert metrics["Open RMAs"] == "1"
    assert metrics["Human escalations"] == "1"


def test_dashboard_window_filters_metrics(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    log_path = tmp_path / "audit_logs.jsonl"
    now = datetime.now(timezone.utc)
    logger.append_event(
        session_summary_event(
            session_id="old-session",
            timestamp=(now - timedelta(days=10)).isoformat(),
            anonymous_user_id="anon_old",
        ),
        log_path=log_path,
    )
    logger.append_event(
        session_summary_event(
            session_id="recent-session",
            timestamp=(now - timedelta(hours=1)).isoformat(),
            anonymous_user_id="anon_recent",
        ),
        log_path=log_path,
    )

    app = AppTest.from_file("app.py", default_timeout=10).run()
    app.session_state[
        admin_dashboard.DASHBOARD_LOG_PATH_STATE_KEY
    ] = str(log_path)
    app.run(timeout=10)
    selectbox_by_label(app, "Dashboard window").set_value(
        "Last 24 hours"
    ).run(timeout=10)

    assert metric_values(app)["Total sessions"] == "1"


def test_refresh_reads_new_session_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    log_path = tmp_path / "audit_logs.jsonl"
    log_path.write_text("", encoding="utf-8")

    app = AppTest.from_file("app.py", default_timeout=10).run()
    app.session_state[
        admin_dashboard.DASHBOARD_LOG_PATH_STATE_KEY
    ] = str(log_path)
    app.run(timeout=10)
    assert metric_values(app)["Total sessions"] == "0"

    logger.append_event(
        session_summary_event(session_id="new-session"),
        log_path=log_path,
    )
    button_by_label(app, "Refresh metrics").click().run(timeout=10)

    assert metric_values(app)["Total sessions"] == "1"
    assert metric_values(app)["Warranty claims"] == "1"


def test_completed_customer_workflow_updates_dashboard(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    db_path = tmp_path / "mock_db.json"
    log_path = tmp_path / "audit_logs.jsonl"
    shutil.copy2(tools.DB_PATH, db_path)
    support_agent = agent.SupportAgent(
        gateway=model_gateway.FakeModelGateway(),
        db_path=db_path,
        log_path=log_path,
    )
    session = support_agent.create_session(
        "dashboard-ui-user",
        "dashboard-ui-session",
    )

    app = AppTest.from_file("app.py", default_timeout=10).run()
    app.session_state[user_view.AGENT_STATE_KEY] = support_agent
    app.session_state[user_view.SESSION_STATE_KEY] = session
    app.session_state[
        admin_dashboard.DASHBOARD_LOG_PATH_STATE_KEY
    ] = str(log_path)
    app.run(timeout=10)
    app.text_area[0].set_value(
        "The VC-R1000 router had a red light, but the power cycle fixed it."
    )
    app.selectbox[0].set_value("Resolved after troubleshooting")
    button_by_label(app, "Analyze support request").click().run(timeout=10)
    button_by_label(app, "Refresh metrics").click().run(timeout=10)

    metrics = metric_values(app)
    assert not app.exception
    assert metrics["Total sessions"] == "1"
    assert metrics["Active users"] == "1"
    assert metrics["Resolution rate"] == "100.0%"
