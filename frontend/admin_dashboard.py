"""Streamlit operational dashboard for VisionCare telemetry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import streamlit as st

from backend import analytics
from backend.logger import AUDIT_LOG_PATH


WINDOW_OPTIONS = ("All time", "Last 24 hours", "Last 7 days")
DASHBOARD_LOG_PATH_STATE_KEY = "visioncare_dashboard_log_path"
AGENT_STATE_KEY = "visioncare_agent"


def render_admin_dashboard(log_path: str | Path | None = None) -> None:
    """Render session-level operational metrics from the JSONL event stream."""
    title_col, refresh_col = st.columns([5, 1])
    with title_col:
        st.header("Operations dashboard")
        st.caption(
            "DuckDB metrics calculated from completed session summaries only."
        )
    with refresh_col:
        st.button(
            "Refresh metrics",
            key="visioncare_refresh_metrics",
            use_container_width=True,
        )

    window_label = st.selectbox(
        "Dashboard window",
        options=WINDOW_OPTIONS,
        key="visioncare_dashboard_window",
    )
    now = datetime.now(timezone.utc)
    start_time, end_time = resolve_time_window(window_label, now=now)
    resolved_log_path = resolve_dashboard_log_path(log_path)

    try:
        metrics = analytics.get_dashboard_metrics(
            resolved_log_path,
            start_time=start_time,
            end_time=end_time,
        )
    except (duckdb.Error, OSError, ValueError) as error:
        st.error(
            "Dashboard telemetry could not be read. Verify the JSONL runtime "
            f"log and retry. ({error.__class__.__name__})"
        )
        return

    _render_metric_cards(metrics)
    if metrics["total_completed_sessions"] == 0:
        st.info(
            "No completed support sessions exist in the selected window. "
            "Tool calls alone are intentionally excluded."
        )

    chart_col, category_col = st.columns(2)
    with chart_col:
        st.subheader("Sentiment distribution")
        _render_distribution(
            metrics["sentiment_counts"],
            empty_message="No sentiment data in this window.",
        )
    with category_col:
        st.subheader("Issue category distribution")
        _render_distribution(
            metrics["issue_category_counts"],
            empty_message="No issue category data in this window.",
        )

    st.caption(
        f"Window: {window_description(window_label, start_time, end_time)}. "
        f"Refreshed {now.strftime('%Y-%m-%d %H:%M:%S UTC')}."
    )


def resolve_time_window(
    window_label: str,
    *,
    now: datetime | None = None,
) -> tuple[datetime | None, datetime | None]:
    """Convert a dashboard window label into UTC analytics boundaries."""
    if window_label not in WINDOW_OPTIONS:
        raise ValueError(f"unsupported dashboard window: {window_label}")
    if window_label == "All time":
        return None, None

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    else:
        current_time = current_time.astimezone(timezone.utc)

    duration = timedelta(hours=24)
    if window_label == "Last 7 days":
        duration = timedelta(days=7)
    return current_time - duration, current_time


def resolve_dashboard_log_path(log_path: str | Path | None = None) -> Path:
    """Resolve an explicit, test-injected, agent, or default telemetry path."""
    if log_path is not None:
        return Path(log_path)

    state_path = st.session_state.get(DASHBOARD_LOG_PATH_STATE_KEY)
    if state_path:
        return Path(state_path)

    agent = st.session_state.get(AGENT_STATE_KEY)
    agent_log_path = getattr(agent, "log_path", None)
    if agent_log_path is not None:
        return Path(agent_log_path)
    return AUDIT_LOG_PATH


def distribution_rows(counts: dict[str, int]) -> list[dict[str, Any]]:
    """Return deterministic chart rows for one categorical distribution."""
    return [
        {"Category": label.replace("_", " ").title(), "Count": int(count)}
        for label, count in sorted(
            counts.items(),
            key=lambda item: (-int(item[1]), item[0]),
        )
    ]


def window_description(
    window_label: str,
    start_time: datetime | None,
    end_time: datetime | None,
) -> str:
    """Format the active analytics window for the dashboard footer."""
    if start_time is None or end_time is None:
        return "all completed sessions"
    return (
        f"{window_label.lower()} "
        f"({start_time.strftime('%Y-%m-%d %H:%M UTC')} to "
        f"{end_time.strftime('%Y-%m-%d %H:%M UTC')})"
    )


def _render_metric_cards(metrics: dict[str, Any]) -> None:
    first_row = st.columns(4)
    first_row[0].metric(
        "Total sessions",
        int(metrics["total_completed_sessions"]),
        border=True,
    )
    first_row[1].metric(
        "Active users",
        int(metrics["active_users"]),
        border=True,
    )
    first_row[2].metric(
        "Resolution rate",
        f"{float(metrics['resolution_rate']) * 100:.1f}%",
        border=True,
    )
    first_row[3].metric(
        "Average handling time",
        f"{float(metrics['average_handling_time_sec']):.1f} sec",
        border=True,
    )

    second_row = st.columns(3)
    second_row[0].metric(
        "Warranty claims",
        int(metrics["warranty_claim_count"]),
        border=True,
    )
    second_row[1].metric(
        "Open RMAs",
        int(metrics["open_rma_count"]),
        border=True,
        help=(
            "V1 has no close-RMA event, so this is the number of distinct RMA "
            "IDs referenced by completed sessions."
        ),
    )
    second_row[2].metric(
        "Human escalations",
        int(metrics["human_escalation_count"]),
        border=True,
    )


def _render_distribution(
    counts: dict[str, int],
    *,
    empty_message: str,
) -> None:
    rows = distribution_rows(counts)
    if not rows:
        st.caption(empty_message)
        return
    st.bar_chart(
        rows,
        x="Category",
        y="Count",
        x_label=None,
        y_label="Sessions",
        color="#4F46E5",
        horizontal=True,
    )
