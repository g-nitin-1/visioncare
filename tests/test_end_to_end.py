import hashlib
import json
import shutil
from pathlib import Path

import cv2
import pytest

from backend import agent, model_gateway, tools
from scripts import live_smoke


FIXTURES_DIR = Path(__file__).with_name("fixtures")
ROUTER_IMAGE = FIXTURES_DIR / "router_red_light.jpg"
VALID_INVOICE = FIXTURES_DIR / "valid_invoice.pdf"
EXPIRED_INVOICE = FIXTURES_DIR / "expired_invoice.pdf"
COMMITTED_FIXTURES = (ROUTER_IMAGE, VALID_INVOICE, EXPIRED_INVOICE)


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_db(db_path):
    with db_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_events(log_path):
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def tool_names(log_path):
    return [
        event["tool_name"]
        for event in read_events(log_path)
        if event["event_type"] == "tool_call"
    ]


@pytest.fixture(scope="module", autouse=True)
def committed_fixtures_remain_unchanged():
    before = {path: file_digest(path) for path in COMMITTED_FIXTURES}
    yield
    after = {path: file_digest(path) for path in COMMITTED_FIXTURES}
    assert after == before


@pytest.fixture()
def workflow_paths(tmp_path):
    db_path = tmp_path / "mock_db.json"
    shutil.copy2(tools.DB_PATH, db_path)
    return db_path, tmp_path / "audit_logs.jsonl"


def make_agent(workflow_paths, gateway=None):
    db_path, log_path = workflow_paths
    return agent.SupportAgent(
        gateway=gateway or model_gateway.FakeModelGateway(),
        db_path=db_path,
        log_path=log_path,
    )


def test_evaluation_fixtures_are_valid_and_in_scope():
    for path in COMMITTED_FIXTURES:
        assert path.exists()
        assert path.stat().st_size > 0

    assert ROUTER_IMAGE.read_bytes().startswith(b"\xff\xd8\xff")
    router_image = cv2.imread(str(ROUTER_IMAGE))
    assert router_image is not None
    assert router_image.shape == (1086, 1448, 3)

    hsv_image = cv2.cvtColor(router_image, cv2.COLOR_BGR2HSV)
    low_red = cv2.inRange(hsv_image, (0, 120, 120), (10, 255, 255))
    high_red = cv2.inRange(hsv_image, (170, 120, 120), (179, 255, 255))
    assert cv2.countNonZero(low_red | high_red) > 100
    assert VALID_INVOICE.read_bytes().startswith(b"%PDF-")
    assert EXPIRED_INVOICE.read_bytes().startswith(b"%PDF-")


def test_router_red_light_returns_manual_steps_without_tool_call(
    workflow_paths,
):
    support_agent = make_agent(workflow_paths)
    session = support_agent.create_session(
        "evaluation-user",
        "e2e-router-troubleshooting",
    )

    result = support_agent.process(
        session,
        "My VC-R1000 router has a red status light.",
        product_image_bytes=ROUTER_IMAGE.read_bytes(),
    )
    _, log_path = workflow_paths
    titles = [section["title"] for section in result["manual_sections"]]

    assert result["status"] == "troubleshooting"
    assert session.intent == "troubleshooting"
    assert titles[0] == "Red Status Light"
    assert "Power Cycle Steps" in titles
    assert tool_names(log_path) == []
    assert read_events(log_path) == []


def test_valid_invoice_shows_warranty_and_calls_only_warranty_tool(
    workflow_paths,
):
    support_agent = make_agent(workflow_paths)
    session = support_agent.create_session(
        "evaluation-user",
        "e2e-valid-warranty",
    )

    result = support_agent.process(
        session,
        "Check the warranty for this invoice.",
        invoice_pdf_bytes=VALID_INVOICE.read_bytes(),
    )
    _, log_path = workflow_paths

    assert result["status"] == "warranty_valid"
    assert session.intent == "warranty_check"
    assert result["warranty"]["valid"] is True
    assert result["warranty"]["expiration_date"] == "2027-03-15"
    assert result["rma"] is None
    assert len(result["sanitized_images"]) == 1
    assert tool_names(log_path) == ["check_warranty_status"]


def test_eligible_unresolved_defect_creates_one_idempotent_rma(
    workflow_paths,
):
    support_agent = make_agent(workflow_paths)
    session = support_agent.create_session(
        "evaluation-user",
        "e2e-eligible-rma",
    )
    request = {
        "message": (
            "The red light remains after troubleshooting. "
            "Please replace the router."
        ),
        "product_image_bytes": ROUTER_IMAGE.read_bytes(),
        "invoice_pdf_bytes": VALID_INVOICE.read_bytes(),
        "issue_resolved": False,
    }

    first = support_agent.process(session, **request)
    second = support_agent.process(session, **request)
    db_path, log_path = workflow_paths
    summaries = [
        event
        for event in read_events(log_path)
        if event["event_type"] == "session_summary"
    ]

    assert first["status"] == "rma_open"
    assert second["status"] == "rma_open"
    assert first["rma"]["status"] == "created"
    assert second["rma"]["status"] == "existing"
    assert first["rma"]["rma_id"] == second["rma"]["rma_id"] == "RMA-0001"
    assert len(read_db(db_path)["rmas"]) == 1
    assert len(summaries) == 1
    assert summaries[0]["intent"] == "warranty_check"
    assert tool_names(log_path) == [
        "check_warranty_status",
        "initiate_rma_process",
        "check_warranty_status",
        "initiate_rma_process",
    ]


def test_expired_invoice_refuses_automatic_rma(workflow_paths):
    support_agent = make_agent(workflow_paths)
    session = support_agent.create_session(
        "evaluation-user",
        "e2e-expired-warranty",
    )

    result = support_agent.process(
        session,
        "The red light remains. Check warranty and create an RMA.",
        product_image_bytes=ROUTER_IMAGE.read_bytes(),
        invoice_pdf_bytes=EXPIRED_INVOICE.read_bytes(),
        issue_resolved=False,
    )
    db_path, log_path = workflow_paths

    assert result["status"] == "warranty_expired"
    assert session.intent == "warranty_check"
    assert result["warranty"]["valid"] is False
    assert result["rma"] is None
    assert "did not open an automatic RMA" in result["response"]
    assert read_db(db_path)["rmas"] == {}
    assert tool_names(log_path) == ["check_warranty_status"]


class UnknownProductGateway(model_gateway.FakeModelGateway):
    def analyze_product_image(self, image_bytes, prompt):
        return {
            "status": "ok",
            "model": self.model,
            "content": "Unknown router fault; model number is unreadable.",
            "product_name": "Unknown Router",
            "issue_category": "unknown_error",
            "issue_keywords": ["unknown"],
            "confidence": 0.9,
        }


def test_unknown_error_after_two_attempts_creates_one_escalation(
    workflow_paths,
):
    support_agent = make_agent(
        workflow_paths,
        gateway=UnknownProductGateway(),
    )
    session = support_agent.create_session(
        "evaluation-user",
        "e2e-unknown-escalation",
    )
    image_bytes = ROUTER_IMAGE.read_bytes()

    first = support_agent.process(
        session,
        "The router has an unknown fault.",
        product_image_bytes=image_bytes,
    )
    second = support_agent.process(
        session,
        "I still cannot identify the model number.",
        product_image_bytes=image_bytes,
    )
    third = support_agent.process(
        session,
        "The model number is still unreadable.",
        product_image_bytes=image_bytes,
    )
    db_path, log_path = workflow_paths
    summaries = [
        event
        for event in read_events(log_path)
        if event["event_type"] == "session_summary"
    ]

    assert first["status"] == "clarification_required"
    assert second["status"] == "escalated"
    assert third["status"] == "escalated"
    assert second["escalation"]["ticket_id"] == "ESC-0001"
    assert third["escalation"]["ticket_id"] == "ESC-0001"
    assert len(read_db(db_path)["escalations"]) == 1
    assert len(summaries) == 1
    assert summaries[0]["intent"] == "human_escalation"
    assert tool_names(log_path) == [
        "escalate_to_human",
        "escalate_to_human",
    ]


def test_live_smoke_runner_uses_committed_router_fixture_offline():
    result = live_smoke.run_smoke(model_gateway.FakeModelGateway())

    assert result["status"] == "ok"
    assert result["model_number"] == "VC-R1000"
    assert result["issue_keywords"] == ["red", "light"]
