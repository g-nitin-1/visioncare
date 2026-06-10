"""DuckDB analytics over VisionCare JSONL telemetry."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from backend.logger import AUDIT_LOG_PATH


WARRANTY_COUNT_INTENTS = ("warranty_check", "warranty_claim", "warranty check")
EVENT_COLUMNS_SQL = """{
    'event_id': 'VARCHAR',
    'schema_version': 'INTEGER',
    'timestamp': 'VARCHAR',
    'session_id': 'VARCHAR',
    'event_type': 'VARCHAR',
    'intent': 'VARCHAR',
    'anonymous_user_id': 'VARCHAR',
    'issue_category': 'VARCHAR',
    'hardware_issue_detected': 'BOOLEAN',
    'sentiment': 'VARCHAR',
    'resolved': 'BOOLEAN',
    'escalated': 'BOOLEAN',
    'rma_id': 'VARCHAR',
    'model': 'VARCHAR',
    'latency_ms': 'DOUBLE',
    'resolution_time_sec': 'DOUBLE'
}"""


def calculate_metrics(
    log_path: str | Path | None = None,
    start_time: str | datetime | None = None,
    end_time: str | datetime | None = None,
) -> dict[str, Any]:
    """Calculate dashboard metrics from session-summary telemetry events."""
    resolved_path = _resolve_log_path(log_path)
    if not resolved_path.exists() or resolved_path.stat().st_size == 0:
        return _empty_metrics()

    with duckdb.connect(database=":memory:") as connection:
        _load_events(connection, resolved_path)
        _create_session_table(connection, start_time, end_time)

        metrics = _summary_metrics(connection)
        metrics["issue_category_counts"] = _count_by(
            connection, "issue_category"
        )
        metrics["sentiment_counts"] = _count_by(connection, "sentiment")
        return metrics


def get_dashboard_metrics(
    log_path: str | Path | None = None,
    start_time: str | datetime | None = None,
    end_time: str | datetime | None = None,
) -> dict[str, Any]:
    """Alias for future Streamlit dashboard code."""
    return calculate_metrics(log_path, start_time, end_time)


def _resolve_log_path(log_path: str | Path | None) -> Path:
    return Path(log_path) if log_path is not None else AUDIT_LOG_PATH


def _empty_metrics() -> dict[str, Any]:
    return {
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


def _load_events(connection: duckdb.DuckDBPyConnection, log_path: Path) -> None:
    connection.execute(
        f"""
        CREATE TEMP TABLE events AS
        SELECT *
        FROM read_json(
            ?,
            format = 'newline_delimited',
            columns = {EVENT_COLUMNS_SQL}
        )
        """,
        [str(log_path)],
    )


def _create_session_table(
    connection: duckdb.DuckDBPyConnection,
    start_time: str | datetime | None,
    end_time: str | datetime | None,
) -> None:
    filters = ["event_type = 'session_summary'"]
    params: list[str] = []

    if start_time is not None:
        filters.append("CAST(timestamp AS TIMESTAMPTZ) >= CAST(? AS TIMESTAMPTZ)")
        params.append(_format_time(start_time))
    if end_time is not None:
        filters.append("CAST(timestamp AS TIMESTAMPTZ) <= CAST(? AS TIMESTAMPTZ)")
        params.append(_format_time(end_time))

    connection.execute(
        f"""
        CREATE TEMP TABLE session_events AS
        SELECT *
        FROM events
        WHERE {" AND ".join(filters)}
        """,
        params,
    )


def _summary_metrics(connection: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    warranty_intents_sql = _sql_string_tuple(WARRANTY_COUNT_INTENTS)
    row = connection.execute(
        f"""
        SELECT
            COUNT(*)::INTEGER AS total_completed_sessions,
            COUNT(DISTINCT anonymous_user_id)::INTEGER AS active_users,
            COALESCE(AVG(CASE WHEN resolved THEN 1.0 ELSE 0.0 END), 0.0)
                AS resolution_rate,
            COALESCE(AVG(resolution_time_sec), 0.0)
                AS average_handling_time_sec,
            COALESCE(SUM(CASE WHEN escalated THEN 1 ELSE 0 END), 0)::INTEGER
                AS human_escalation_count,
            COALESCE(
                SUM(
                    CASE
                        WHEN lower(intent) IN {warranty_intents_sql}
                        THEN 1
                        ELSE 0
                    END
                ),
                0
            )::INTEGER AS warranty_claim_count,
            -- V1 has no RMA close event, so open means referenced by any summary.
            COUNT(DISTINCT NULLIF(rma_id, ''))::INTEGER AS open_rma_count
        FROM session_events
        """
    ).fetchone()

    if row is None:
        return _empty_metrics()

    return {
        "total_completed_sessions": int(row[0]),
        "active_users": int(row[1]),
        "resolution_rate": float(row[2]),
        "average_handling_time_sec": float(row[3]),
        "human_escalation_count": int(row[4]),
        "warranty_claim_count": int(row[5]),
        "open_rma_count": int(row[6]),
    }


def _count_by(connection: duckdb.DuckDBPyConnection, field_name: str) -> dict[str, int]:
    rows = connection.execute(
        f"""
        SELECT {field_name}, COUNT(*)::INTEGER
        FROM session_events
        WHERE {field_name} IS NOT NULL AND {field_name} != ''
        GROUP BY {field_name}
        ORDER BY {field_name}
        """
    ).fetchall()
    return {str(label): int(count) for label, count in rows}


def _format_time(value: str | datetime) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _sql_string_tuple(values: tuple[str, ...]) -> str:
    quoted_values = ", ".join(f"'{value}'" for value in values)
    return f"({quoted_values})"
