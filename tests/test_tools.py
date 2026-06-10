import json
import shutil

import pytest

from backend import tools


@pytest.fixture()
def temp_db_path(tmp_path):
    db_path = tmp_path / "mock_db.json"
    shutil.copy2(tools.DB_PATH, db_path)
    return db_path


def read_db(db_path):
    with db_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def test_valid_serial_number_returns_active_warranty(temp_db_path):
    result = tools.check_warranty_status(
        "vc1000-valid-001", db_path=temp_db_path
    )

    assert result == {
        "status": "valid",
        "serial_number": "VC1000-VALID-001",
        "valid": True,
        "expiration_date": "2027-03-15",
        "product_id": "router-demo-1000",
    }


def test_expired_serial_number_returns_expired_warranty(temp_db_path):
    result = tools.check_warranty_status(
        "VC1000-EXPIRED-001", db_path=temp_db_path
    )

    assert result["status"] == "expired"
    assert result["valid"] is False
    assert result["expiration_date"] == "2024-03-15"
    assert result["product_id"] == "router-demo-1000"


def test_unknown_serial_number_returns_not_found(temp_db_path):
    result = tools.check_warranty_status("missing-serial", db_path=temp_db_path)

    assert result == {
        "status": "not_found",
        "serial_number": "MISSING-SERIAL",
        "valid": False,
        "expiration_date": None,
        "product_id": None,
    }


def test_rma_request_creates_one_record(temp_db_path):
    result = tools.initiate_rma_process(
        user_id="user-123",
        product_id="router-demo-1000",
        defect_description="Router red light remains on after reboot.",
        idempotency_key="session-1:rma",
        db_path=temp_db_path,
    )
    db = read_db(temp_db_path)

    assert result["status"] == "created"
    assert result["rma_id"] == "RMA-0001"
    assert len(db["rmas"]) == 1
    assert db["rmas"]["session-1:rma"]["rma_id"] == "RMA-0001"


def test_repeated_rma_request_returns_existing_record(temp_db_path):
    first = tools.initiate_rma_process(
        "user-123",
        "router-demo-1000",
        "Router red light remains on after reboot.",
        "session-1:rma",
        db_path=temp_db_path,
    )
    second = tools.initiate_rma_process(
        "user-123",
        "router-demo-1000",
        "Router red light remains on after reboot.",
        "session-1:rma",
        db_path=temp_db_path,
    )
    db = read_db(temp_db_path)

    assert first["rma_id"] == second["rma_id"]
    assert second["status"] == "existing"
    assert len(db["rmas"]) == 1


def test_rma_request_rejects_unknown_product(temp_db_path):
    result = tools.initiate_rma_process(
        "user-123",
        "unknown-product",
        "Broken device.",
        "session-2:rma",
        db_path=temp_db_path,
    )

    assert result == {
        "status": "not_found",
        "error": "unknown_product",
        "product_id": "unknown-product",
        "rma_id": None,
    }
    assert read_db(temp_db_path)["rmas"] == {}


def test_escalation_request_creates_one_ticket(temp_db_path):
    result = tools.escalate_to_human(
        priority_level="High",
        chat_history_summary="User remains blocked after two attempts.",
        idempotency_key="session-1:escalation",
        db_path=temp_db_path,
    )
    db = read_db(temp_db_path)

    assert result == {
        "status": "created",
        "ticket_id": "ESC-0001",
        "assigned_queue": "hardware_support",
        "idempotency_key": "session-1:escalation",
    }
    assert len(db["escalations"]) == 1


def test_repeated_escalation_request_returns_existing_ticket(temp_db_path):
    first = tools.escalate_to_human(
        "Low",
        "User asks for a human agent.",
        "session-2:escalation",
        db_path=temp_db_path,
    )
    second = tools.escalate_to_human(
        "Low",
        "User asks for a human agent.",
        "session-2:escalation",
        db_path=temp_db_path,
    )
    db = read_db(temp_db_path)

    assert first["ticket_id"] == second["ticket_id"]
    assert second["status"] == "existing"
    assert len(db["escalations"]) == 1


def test_required_tool_inputs_reject_empty_values(temp_db_path):
    with pytest.raises(ValueError, match="serial_number is required"):
        tools.check_warranty_status("", db_path=temp_db_path)

    with pytest.raises(ValueError, match="idempotency_key is required"):
        tools.initiate_rma_process(
            "user-123",
            "router-demo-1000",
            "Broken device.",
            None,
            db_path=temp_db_path,
        )
