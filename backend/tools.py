"""Offline tool functions for the VisionCare demo backend."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from filelock import FileLock


DB_PATH = Path(__file__).with_name("mock_db.json")


def check_warranty_status(
    serial_number: str, db_path: str | Path | None = None
) -> dict[str, Any]:
    """Return warranty details for a serial number from the local mock DB."""
    normalized_serial = _normalize_required("serial_number", serial_number).upper()
    db = _load_db(_resolve_db_path(db_path))

    warranty = db["warranties"].get(normalized_serial)
    if warranty is None:
        return {
            "status": "not_found",
            "serial_number": normalized_serial,
            "valid": False,
            "expiration_date": None,
            "product_id": None,
        }

    valid = bool(warranty["valid"])
    return {
        "status": "valid" if valid else "expired",
        "serial_number": normalized_serial,
        "valid": valid,
        "expiration_date": warranty["expiration_date"],
        "product_id": warranty["product_id"],
    }


def initiate_rma_process(
    user_id: str,
    product_id: str,
    defect_description: str,
    idempotency_key: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create or return an idempotent RMA record for an eligible request."""
    resolved_path = _resolve_db_path(db_path)
    normalized_user_id = _normalize_required("user_id", user_id)
    normalized_product_id = _normalize_required("product_id", product_id)
    normalized_description = _normalize_required(
        "defect_description", defect_description
    )
    normalized_key = _normalize_required("idempotency_key", idempotency_key)

    with _db_lock(resolved_path):
        db = _load_db(resolved_path)

        if normalized_product_id not in db["products"]:
            return {
                "status": "not_found",
                "error": "unknown_product",
                "product_id": normalized_product_id,
                "rma_id": None,
            }

        existing = db["rmas"].get(normalized_key)
        if existing is not None:
            return {
                "status": "existing",
                "rma_id": existing["rma_id"],
                "product_id": existing["product_id"],
                "idempotency_key": normalized_key,
            }

        rma_id = _next_identifier("RMA", db["rmas"].values(), "rma_id")
        record = {
            "rma_id": rma_id,
            "user_id": normalized_user_id,
            "product_id": normalized_product_id,
            "defect_description": normalized_description,
            "idempotency_key": normalized_key,
            "status": "open",
        }
        db["rmas"][normalized_key] = record
        _save_db(resolved_path, db)

    return {
        "status": "created",
        "rma_id": rma_id,
        "product_id": normalized_product_id,
        "idempotency_key": normalized_key,
    }


def escalate_to_human(
    priority_level: str,
    chat_history_summary: str,
    idempotency_key: str,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create or return an idempotent human escalation ticket."""
    resolved_path = _resolve_db_path(db_path)
    normalized_priority = _normalize_priority(priority_level)
    normalized_summary = _normalize_required(
        "chat_history_summary", chat_history_summary
    )
    normalized_key = _normalize_required("idempotency_key", idempotency_key)

    with _db_lock(resolved_path):
        db = _load_db(resolved_path)

        existing = db["escalations"].get(normalized_key)
        if existing is not None:
            return {
                "status": "existing",
                "ticket_id": existing["ticket_id"],
                "assigned_queue": existing["assigned_queue"],
                "idempotency_key": normalized_key,
            }

        ticket_id = _next_identifier(
            "ESC", db["escalations"].values(), "ticket_id"
        )
        record = {
            "ticket_id": ticket_id,
            "priority_level": normalized_priority,
            "chat_history_summary": normalized_summary,
            "idempotency_key": normalized_key,
            "assigned_queue": "hardware_support",
            "status": "open",
        }
        db["escalations"][normalized_key] = record
        _save_db(resolved_path, db)

    return {
        "status": "created",
        "ticket_id": ticket_id,
        "assigned_queue": "hardware_support",
        "idempotency_key": normalized_key,
    }


def _resolve_db_path(db_path: str | Path | None) -> Path:
    return Path(db_path) if db_path is not None else DB_PATH


def _db_lock(db_path: Path) -> FileLock:
    return FileLock(f"{db_path}.lock")


def _load_db(db_path: Path) -> dict[str, Any]:
    with db_path.open("r", encoding="utf-8") as handle:
        db = json.load(handle)

    for key in ("products", "warranties", "rmas", "escalations"):
        if not isinstance(db.get(key), dict):
            raise ValueError(f"mock DB is missing object field: {key}")

    return db


def _save_db(db_path: Path, db: dict[str, Any]) -> None:
    tmp_path = db_path.with_name(f"{db_path.name}.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(db, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(tmp_path, db_path)


def _next_identifier(
    prefix: str, records: Any, identifier_field: str
) -> str:
    highest = 0
    for record in records:
        identifier = str(record.get(identifier_field, ""))
        expected_prefix = f"{prefix}-"
        if not identifier.startswith(expected_prefix):
            continue
        suffix = identifier.removeprefix(expected_prefix)
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return f"{prefix}-{highest + 1:04d}"


def _normalize_required(field_name: str, value: object) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_priority(priority_level: str) -> str:
    normalized = _normalize_required("priority_level", priority_level).lower()
    if normalized not in {"low", "high"}:
        raise ValueError("priority_level must be Low or High")
    return normalized.capitalize()
