import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from backend import agent, model_gateway, tools


FIXTURES_DIR = Path(__file__).with_name("fixtures")


def read_db(db_path):
    with db_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_jsonl(log_path):
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@pytest.fixture()
def temp_db_path(tmp_path):
    db_path = tmp_path / "mock_db.json"
    shutil.copy2(tools.DB_PATH, db_path)
    return db_path


@pytest.fixture()
def log_path(tmp_path):
    return tmp_path / "audit_logs.jsonl"


def sanitized_document(serial_number="VC1000-VALID-001"):
    purchase_date = (
        "2023-01-15" if "EXPIRED" in serial_number else "2026-01-15"
    )

    def sanitize(_pdf_bytes):
        return {
            "sanitized_images": [
                np.full((80, 120, 3), 255, dtype=np.uint8)
            ],
            "extracted_fields": {
                "product_name": "VisionCare Demo Router",
                "model_number": "VC-R1000",
                "purchase_date": purchase_date,
                "serial_number": serial_number,
            },
            "verification": {
                "passed": True,
                "remaining_values": [],
            },
        }

    return sanitize


def make_agent(temp_db_path, log_path, **overrides):
    options = {
        "gateway": model_gateway.FakeModelGateway(),
        "db_path": temp_db_path,
        "log_path": log_path,
        "document_sanitizer": sanitized_document(),
    }
    options.update(overrides)
    return agent.SupportAgent(**options)


def test_valid_warranty_and_unresolved_defect_creates_one_rma(
    temp_db_path, log_path
):
    support_agent = make_agent(temp_db_path, log_path)
    session = support_agent.create_session("customer-123", "session-valid")

    result = support_agent.process(
        session,
        "The red light remains after troubleshooting. Please replace it.",
        product_image_bytes=model_gateway_test_png(),
        invoice_pdf_bytes=b"synthetic-valid-invoice",
        issue_resolved=False,
    )
    db = read_db(temp_db_path)
    events = read_jsonl(log_path)

    assert result["status"] == "rma_open"
    assert result["warranty"]["status"] == "valid"
    assert result["rma"]["status"] == "created"
    assert result["rma"]["rma_id"] == "RMA-0001"
    assert session.intent == "warranty_check"
    assert session.resolved is True
    assert len(db["rmas"]) == 1
    assert [event["event_type"] for event in events] == [
        "tool_call",
        "tool_call",
        "session_summary",
    ]
    assert events[-1]["intent"] == "warranty_check"
    assert events[-1]["rma_id"] == "RMA-0001"
    assert "user_id" not in events[-1]


def test_repeating_request_returns_same_rma_and_one_summary(
    temp_db_path, log_path
):
    support_agent = make_agent(temp_db_path, log_path)
    session = support_agent.create_session("customer-123", "session-repeat")
    kwargs = {
        "product_image_bytes": model_gateway_test_png(),
        "invoice_pdf_bytes": b"synthetic-valid-invoice",
        "issue_resolved": False,
    }

    first = support_agent.process(
        session,
        "The red light is still red. I need a replacement.",
        **kwargs,
    )
    second = support_agent.process(
        session,
        "The red light is still red. I need a replacement.",
        **kwargs,
    )
    events = read_jsonl(log_path)

    assert first["rma"]["rma_id"] == second["rma"]["rma_id"]
    assert second["rma"]["status"] == "existing"
    assert len(read_db(temp_db_path)["rmas"]) == 1
    assert sum(event["event_type"] == "session_summary" for event in events) == 1


def test_expired_warranty_never_creates_rma(temp_db_path, log_path):
    support_agent = make_agent(
        temp_db_path,
        log_path,
        document_sanitizer=sanitized_document("VC1000-EXPIRED-001"),
    )
    session = support_agent.create_session("customer-123", "session-expired")

    result = support_agent.process(
        session,
        "The red light remains after troubleshooting. Please create an RMA.",
        product_image_bytes=model_gateway_test_png(),
        invoice_pdf_bytes=b"synthetic-expired-invoice",
        issue_resolved=False,
    )
    events = read_jsonl(log_path)

    assert result["status"] == "warranty_expired"
    assert result["warranty"]["valid"] is False
    assert result["rma"] is None
    assert session.rma_id is None
    assert read_db(temp_db_path)["rmas"] == {}
    assert [event["tool_name"] for event in events if event["event_type"] == "tool_call"] == [
        "check_warranty_status"
    ]
    assert "did not open an automatic RMA" in result["response"]


class ProductNameOnlyGateway(model_gateway.FakeModelGateway):
    def analyze_product_image(self, image_bytes, prompt):
        return {
            "status": "ok",
            "model": self.model,
            "content": "VisionCare Demo Router with an unknown fault.",
            "product_name": "VisionCare Demo Router",
            "issue_category": "unknown_error",
            "issue_keywords": ["mystery"],
            "confidence": 0.9,
        }


def test_product_name_without_model_number_asks_for_clarification(
    temp_db_path, log_path
):
    support_agent = make_agent(
        temp_db_path,
        log_path,
        gateway=ProductNameOnlyGateway(),
    )
    session = support_agent.create_session("customer-123", "session-unknown")

    result = support_agent.process(
        session,
        "This router has an unknown problem.",
        product_image_bytes=model_gateway_test_png(),
    )

    assert result["status"] == "clarification_required"
    assert result["product"]["status"] == "clarification_required"
    assert "model number" in result["response"].lower()
    assert session.diagnosis_attempts == 1
    assert read_jsonl(log_path) == []


def test_two_failed_diagnosis_attempts_create_one_escalation(
    temp_db_path, log_path
):
    support_agent = make_agent(
        temp_db_path,
        log_path,
        gateway=ProductNameOnlyGateway(),
    )
    session = support_agent.create_session("customer-123", "session-escalate")

    first = support_agent.process(
        session,
        "This router has an unknown problem.",
        product_image_bytes=model_gateway_test_png(),
    )
    second = support_agent.process(
        session,
        "I still cannot find the model number.",
        product_image_bytes=model_gateway_test_png(),
    )
    third = support_agent.process(
        session,
        "I still cannot find the model number.",
        product_image_bytes=model_gateway_test_png(),
    )
    db = read_db(temp_db_path)
    events = read_jsonl(log_path)

    assert first["status"] == "clarification_required"
    assert second["status"] == "escalated"
    assert third["status"] == "escalated"
    assert second["escalation"]["ticket_id"] == third["escalation"]["ticket_id"]
    assert len(db["escalations"]) == 1
    assert session.diagnosis_attempts == 3
    assert sum(event["event_type"] == "session_summary" for event in events) == 1


class LowConfidenceGateway(model_gateway.FakeModelGateway):
    def analyze_product_image(self, image_bytes, prompt):
        result = super().analyze_product_image(image_bytes, prompt)
        result["confidence"] = 0.2
        return result


def test_low_confidence_escalates_immediately(temp_db_path, log_path):
    support_agent = make_agent(
        temp_db_path,
        log_path,
        gateway=LowConfidenceGateway(),
    )
    session = support_agent.create_session("customer-123", "session-low-confidence")

    result = support_agent.process(
        session,
        "The router has a red light.",
        product_image_bytes=model_gateway_test_png(),
    )

    assert result["status"] == "escalated"
    assert result["escalation"]["ticket_id"] == "ESC-0001"
    assert read_db(temp_db_path)["escalations"][
        "session-low-confidence:escalation"
    ]["priority_level"] == "High"


def test_highly_negative_sentiment_escalates_immediately(
    temp_db_path, log_path
):
    support_agent = make_agent(temp_db_path, log_path)
    session = support_agent.create_session("customer-123", "session-angry")

    result = support_agent.process(
        session,
        "This router problem is unacceptable.",
        product_image_bytes=model_gateway_test_png(),
    )

    assert result["status"] == "escalated"
    assert session.sentiment == "highly_negative"
    assert read_db(temp_db_path)["escalations"][
        "session-angry:escalation"
    ]["priority_level"] == "High"


class TrackingGateway(model_gateway.FakeModelGateway):
    def __init__(self):
        self.document_calls = 0

    def extract_document_fields(self, sanitized_images, prompt):
        self.document_calls += 1
        return super().extract_document_fields(sanitized_images, prompt)


def test_failed_redaction_blocks_document_model_call(temp_db_path, log_path):
    gateway = TrackingGateway()

    def failed_sanitizer(_pdf_bytes):
        return {
            "sanitized_images": [],
            "extracted_fields": {},
            "verification": {
                "passed": False,
                "remaining_values": ["Jane Demo"],
            },
        }

    support_agent = make_agent(
        temp_db_path,
        log_path,
        gateway=gateway,
        document_sanitizer=failed_sanitizer,
    )
    session = support_agent.create_session("customer-123", "session-privacy")

    with pytest.raises(agent.PrivacyVerificationError):
        support_agent.process(
            session,
            "Check this invoice.",
            invoice_pdf_bytes=b"unsafe-invoice",
        )

    assert gateway.document_calls == 0
    assert read_jsonl(log_path) == []


def test_completed_workflow_appends_exactly_one_summary(temp_db_path, log_path):
    support_agent = make_agent(temp_db_path, log_path)
    session = support_agent.create_session("customer-123", "session-summary")

    result = support_agent.process(
        session,
        "The VC-R1000 has a red status light.",
        product_image_bytes=model_gateway_test_png(),
        issue_resolved=True,
    )
    summaries = [
        event
        for event in read_jsonl(log_path)
        if event["event_type"] == "session_summary"
    ]

    assert result["status"] == "resolved"
    assert len(result["manual_sections"]) > 0
    assert len(summaries) == 1
    assert summaries[0]["anonymous_user_id"].startswith("anon_")
    assert summaries[0]["model"] == "fake/visioncare-demo"


def test_troubleshooting_is_logged_only_after_outcome_is_known(
    temp_db_path, log_path
):
    support_agent = make_agent(temp_db_path, log_path)
    session = support_agent.create_session("customer-123", "session-multiturn")

    first = support_agent.process(
        session,
        "The VC-R1000 has a red status light.",
        product_image_bytes=model_gateway_test_png(),
    )
    second = support_agent.process(
        session,
        "The power cycle fixed the VC-R1000.",
        product_image_bytes=model_gateway_test_png(),
        issue_resolved=True,
    )
    summaries = [
        event
        for event in read_jsonl(log_path)
        if event["event_type"] == "session_summary"
    ]

    assert first["status"] == "troubleshooting"
    assert second["status"] == "resolved"
    assert len(summaries) == 1
    assert summaries[0]["resolved"] is True


def test_real_privacy_pipeline_connects_invoice_to_agent(
    temp_db_path, log_path
):
    support_agent = agent.SupportAgent(
        gateway=model_gateway.FakeModelGateway(),
        db_path=temp_db_path,
        log_path=log_path,
    )
    session = support_agent.create_session("customer-123", "session-real-privacy")

    result = support_agent.process(
        session,
        "The red light remains after troubleshooting.",
        invoice_pdf_bytes=(FIXTURES_DIR / "valid_invoice.pdf").read_bytes(),
        issue_resolved=False,
    )

    assert result["status"] == "rma_open"
    assert result["warranty"]["status"] == "valid"
    assert result["rma"]["rma_id"] == "RMA-0001"
    assert len(result["sanitized_images"]) == 1


def model_gateway_test_png():
    import cv2

    image = np.full((120, 280, 3), 255, dtype=np.uint8)
    cv2.putText(
        image,
        "VC-R1000",
        (20, 70),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 0, 0),
        2,
        cv2.LINE_AA,
    )
    success, encoded = cv2.imencode(".png", image)
    assert success
    return encoded.tobytes()
