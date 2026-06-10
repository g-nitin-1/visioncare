import json
import shutil
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from backend import agent, model_gateway, tools
from backend.model_gateway import ModelGatewayError
from frontend import user_view


def upload(name, mime_type, data):
    return SimpleNamespace(
        name=name,
        type=mime_type,
        getvalue=lambda: data,
    )


@pytest.mark.parametrize(
    ("name", "mime_type", "data"),
    [
        ("router.png", "image/png", b"\x89PNG\r\n\x1a\ncontent"),
        ("router.jpg", "image/jpeg", b"\xff\xd8\xffcontent"),
        ("router.webp", "image/webp", b"RIFF1234WEBPcontent"),
        ("router.gif", "image/gif", b"GIF89acontent"),
    ],
)
def test_supported_router_uploads_are_accepted(name, mime_type, data):
    assert user_view.validate_uploaded_file(
        upload(name, mime_type, data), "image"
    ) == data


def test_invoice_upload_requires_pdf_magic_bytes():
    with pytest.raises(user_view.UploadValidationError, match="valid PDF"):
        user_view.validate_uploaded_file(
            upload("invoice.pdf", "application/pdf", b"not-a-pdf"),
            "invoice",
        )


def test_supported_invoice_upload_is_accepted():
    data = b"%PDF-1.4\nsynthetic"

    assert user_view.validate_uploaded_file(
        upload("invoice.pdf", "application/pdf", data),
        "invoice",
    ) == data


def test_unsupported_image_extension_is_rejected():
    with pytest.raises(user_view.UploadValidationError, match="must use"):
        user_view.validate_uploaded_file(
            upload(
                "router.txt",
                "image/png",
                b"\x89PNG\r\n\x1a\ncontent",
            ),
            "image",
        )


def test_oversized_upload_is_rejected(monkeypatch):
    monkeypatch.setattr(user_view, "MAX_IMAGE_BYTES", 8)

    with pytest.raises(user_view.UploadValidationError, match="exceeds"):
        user_view.validate_uploaded_file(
            upload(
                "router.png",
                "image/png",
                b"\x89PNG\r\n\x1a\ncontent",
            ),
            "image",
        )


def test_provider_error_message_does_not_expose_provider_payload():
    message = user_view.provider_error_message(
        ModelGatewayError("HTTP 429: upstream secret payload")
    )

    assert "temporarily unavailable" in message
    assert "HTTP 429" not in message
    assert "secret payload" not in message
    assert "MODEL_PROVIDER=fake" in message


def test_streamlit_app_loads_without_exceptions(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "fake")
    app = AppTest.from_file("app.py", default_timeout=10).run()

    assert not app.exception
    assert app.title[0].value == "VisionCare"
    assert app.header[0].value == "Customer support"
    assert app.button[0].label == "New session"
    assert "Offline demo mode is active" in app.info[0].value


def test_streamlit_form_runs_resolved_text_workflow(tmp_path):
    db_path = tmp_path / "mock_db.json"
    log_path = tmp_path / "audit_logs.jsonl"
    shutil.copy2(tools.DB_PATH, db_path)
    support_agent = agent.SupportAgent(
        gateway=model_gateway.FakeModelGateway(),
        db_path=db_path,
        log_path=log_path,
    )
    session = support_agent.create_session("ui-test-user", "ui-test-session")

    app = AppTest.from_file("app.py", default_timeout=10).run()
    app.session_state[user_view.AGENT_STATE_KEY] = support_agent
    app.session_state[user_view.SESSION_STATE_KEY] = session
    app.text_area[0].set_value(
        "The VC-R1000 router has a red status light."
    )
    app.selectbox[0].set_value("Resolved after troubleshooting")
    app.button[1].click().run(timeout=10)

    assert not app.exception
    assert any("Issue resolved" in item.value for item in app.success)
    assert app.session_state[user_view.SESSION_STATE_KEY].resolved is True
    assert log_path.exists()

    completed_session_id = app.session_state[
        user_view.SESSION_STATE_KEY
    ].session_id
    app.button[0].click().run(timeout=10)

    new_session = app.session_state[user_view.SESSION_STATE_KEY]
    assert new_session.session_id != completed_session_id
    assert new_session.messages == []


def test_streamlit_repeat_submit_keeps_one_rma(tmp_path):
    db_path = tmp_path / "mock_db.json"
    log_path = tmp_path / "audit_logs.jsonl"
    shutil.copy2(tools.DB_PATH, db_path)
    support_agent = agent.SupportAgent(
        gateway=model_gateway.FakeModelGateway(),
        db_path=db_path,
        log_path=log_path,
    )
    session = support_agent.create_session("ui-rma-user", "ui-rma-session")

    app = AppTest.from_file("app.py", default_timeout=10).run()
    app.session_state[user_view.AGENT_STATE_KEY] = support_agent
    app.session_state[user_view.SESSION_STATE_KEY] = session
    app.text_area[0].set_value(
        "The VC-R1000 is still red after troubleshooting. "
        "Serial VC1000-VALID-001. Please replace it."
    )
    app.selectbox[0].set_value("Still broken after troubleshooting")
    app.button[1].click().run(timeout=10)
    app.button[1].click().run(timeout=10)

    with db_path.open("r", encoding="utf-8") as handle:
        db = json.load(handle)

    assert not app.exception
    assert len(db["rmas"]) == 1
    assert app.session_state[user_view.SESSION_STATE_KEY].rma_id == "RMA-0001"
    assert any("RMA-0001" in item.value for item in app.success)


class FailingAgent:
    gateway = SimpleNamespace(model="test/live-provider")

    def create_session(self, user_id):
        return agent.AgentSessionState(user_id=user_id)

    def process(self, session, message, **kwargs):
        session.messages.append({"role": "user", "content": message})
        raise ModelGatewayError("HTTP 429: sensitive upstream response")


def test_streamlit_provider_error_is_friendly_and_form_remains_usable():
    failing_agent = FailingAgent()
    session = failing_agent.create_session("ui-provider-test")

    app = AppTest.from_file("app.py", default_timeout=10).run()
    app.session_state[user_view.AGENT_STATE_KEY] = failing_agent
    app.session_state[user_view.SESSION_STATE_KEY] = session
    app.text_area[0].set_value("The VC-R1000 router has a red light.")
    app.button[1].click().run(timeout=10)

    assert not app.exception
    assert len(app.error) == 1
    assert "temporarily unavailable" in app.error[0].value
    assert "HTTP 429" not in app.error[0].value
    assert app.button[1].label == "Analyze support request"
    rolled_back = app.session_state[user_view.SESSION_STATE_KEY]
    assert rolled_back.messages == []
