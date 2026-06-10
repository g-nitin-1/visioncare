"""Streamlit customer view for the VisionCare support workflow."""

from __future__ import annotations

from copy import deepcopy
import uuid
from pathlib import Path
from typing import Any

import streamlit as st

from backend import privacy
from backend.agent import AgentError, AgentSessionState, SupportAgent
from backend.model_gateway import ModelGatewayError


MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PDF_BYTES = 10 * 1024 * 1024
IMAGE_EXTENSIONS = {".gif", ".jpeg", ".jpg", ".png", ".webp"}
IMAGE_MIME_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}
GENERIC_MIME_TYPES = {"", "application/octet-stream"}
OUTCOME_OPTIONS = {
    "Not confirmed yet": None,
    "Still broken after troubleshooting": False,
    "Resolved after troubleshooting": True,
}
AGENT_STATE_KEY = "visioncare_agent"
SESSION_STATE_KEY = "visioncare_support_session"
USER_STATE_KEY = "visioncare_user_reference"
RESULT_STATE_KEY = "visioncare_last_result"


class UploadValidationError(ValueError):
    """Raised when an uploaded file is unsafe or unsupported."""


def render_user_view() -> None:
    """Render the customer support workflow."""
    _initialize_session_state()

    title_col, action_col = st.columns([5, 1])
    with title_col:
        st.header("Customer support")
        st.caption(
            "Upload a router photo or synthetic invoice. Invoice PII is "
            "redacted locally before model processing."
        )
    with action_col:
        st.button(
            "New session",
            on_click=_reset_support_session,
            use_container_width=True,
        )

    _render_provider_notice(st.session_state[AGENT_STATE_KEY])
    _render_chat_history(st.session_state[SESSION_STATE_KEY])

    with st.form("visioncare_support_form"):
        message = st.text_area(
            "Describe the problem",
            placeholder=(
                "Example: My VC-R1000 still has a red status light after a "
                "power cycle. Please check warranty and replacement options."
            ),
            height=110,
        )
        upload_col, invoice_col = st.columns(2)
        with upload_col:
            product_upload = st.file_uploader(
                "Router photo",
                type=["png", "jpg", "jpeg", "webp", "gif"],
                help=f"PNG, JPEG, WebP, or GIF. Maximum {_format_bytes(MAX_IMAGE_BYTES)}.",
            )
        with invoice_col:
            invoice_upload = st.file_uploader(
                "Synthetic invoice",
                type=["pdf"],
                help=(
                    "PDF only. PII is redacted locally. Maximum "
                    f"{_format_bytes(MAX_PDF_BYTES)}."
                ),
            )

        outcome_label = st.selectbox(
            "Troubleshooting outcome",
            options=list(OUTCOME_OPTIONS),
            help=(
                "Choose 'Still broken' only after the displayed manual steps "
                "have been attempted."
            ),
        )
        submitted = st.form_submit_button(
            "Analyze support request",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        _handle_submission(
            message=message,
            product_upload=product_upload,
            invoice_upload=invoice_upload,
            issue_resolved=OUTCOME_OPTIONS[outcome_label],
        )
    else:
        previous_result = st.session_state.get(RESULT_STATE_KEY)
        if previous_result is not None:
            _render_result(previous_result)


def validate_uploaded_file(upload: Any, upload_kind: str) -> bytes | None:
    """Validate one Streamlit-style upload and return an immutable byte copy."""
    if upload is None:
        return None
    if upload_kind not in {"image", "invoice"}:
        raise ValueError(f"unsupported upload_kind: {upload_kind}")

    data = bytes(upload.getvalue())
    filename = str(getattr(upload, "name", "") or "")
    mime_type = str(getattr(upload, "type", "") or "").lower()
    extension = Path(filename).suffix.lower()

    if not data:
        raise UploadValidationError(f"{upload_kind} upload is empty")

    if upload_kind == "image":
        if len(data) > MAX_IMAGE_BYTES:
            raise UploadValidationError(
                f"router photo exceeds the {_format_bytes(MAX_IMAGE_BYTES)} limit"
            )
        if extension not in IMAGE_EXTENSIONS:
            raise UploadValidationError(
                "router photo must use PNG, JPEG, WebP, or GIF"
            )
        if mime_type not in IMAGE_MIME_TYPES | GENERIC_MIME_TYPES:
            raise UploadValidationError(
                f"unsupported router photo content type: {mime_type}"
            )
        if not _is_supported_image(data):
            raise UploadValidationError(
                "router photo content is not a supported image"
            )
        return data

    if len(data) > MAX_PDF_BYTES:
        raise UploadValidationError(
            f"invoice exceeds the {_format_bytes(MAX_PDF_BYTES)} limit"
        )
    if extension != ".pdf":
        raise UploadValidationError("invoice must use the PDF file extension")
    if mime_type not in {"application/pdf"} | GENERIC_MIME_TYPES:
        raise UploadValidationError(
            f"unsupported invoice content type: {mime_type}"
        )
    if not data.startswith(b"%PDF-"):
        raise UploadValidationError("invoice content is not a valid PDF")
    return data


def provider_error_message(error: ModelGatewayError) -> str:
    """Return a stable user-facing message without exposing provider payloads."""
    return (
        "The model provider is temporarily unavailable. Your request was not "
        "completed. Retry shortly or set MODEL_PROVIDER=fake for the "
        "deterministic offline demo."
    )


def _initialize_session_state() -> None:
    if USER_STATE_KEY not in st.session_state:
        st.session_state[USER_STATE_KEY] = f"demo-user-{uuid.uuid4().hex}"
    if AGENT_STATE_KEY not in st.session_state:
        st.session_state[AGENT_STATE_KEY] = SupportAgent()
    if SESSION_STATE_KEY not in st.session_state:
        st.session_state[SESSION_STATE_KEY] = st.session_state[
            AGENT_STATE_KEY
        ].create_session(st.session_state[USER_STATE_KEY])


def _reset_support_session() -> None:
    agent = st.session_state.get(AGENT_STATE_KEY)
    user_reference = st.session_state.get(USER_STATE_KEY)
    if agent is not None and user_reference:
        st.session_state[SESSION_STATE_KEY] = agent.create_session(user_reference)
    st.session_state.pop(RESULT_STATE_KEY, None)


def _render_provider_notice(agent: SupportAgent) -> None:
    model_name = str(
        getattr(agent.gateway, "model", agent.gateway.__class__.__name__)
    )
    if model_name == "fake/visioncare-demo":
        st.info(
            "Offline demo mode is active. Responses are deterministic and use "
            "no API credits.",
        )
    else:
        st.warning(
            f"Live model mode is active: {model_name}. Free-provider latency "
            "and availability can vary.",
        )


def _render_chat_history(session: AgentSessionState) -> None:
    if not session.messages:
        with st.chat_message("assistant"):
            st.write(
                "Describe the router issue and optionally upload a product "
                "photo or synthetic invoice."
            )
        return

    for message in session.messages:
        role = message.get("role", "assistant")
        with st.chat_message(role):
            st.write(message.get("content", ""))


def _handle_submission(
    *,
    message: str,
    product_upload: Any,
    invoice_upload: Any,
    issue_resolved: bool | None,
) -> None:
    if not message.strip():
        st.error("Describe the support problem before submitting.")
        return

    try:
        image_bytes = validate_uploaded_file(product_upload, "image")
        invoice_bytes = validate_uploaded_file(invoice_upload, "invoice")
    except UploadValidationError as error:
        st.error(str(error))
        return

    raw_invoice_pages: list[Any] = []
    session_before_request = deepcopy(st.session_state[SESSION_STATE_KEY])
    try:
        with st.status("Processing support request", expanded=True) as status:
            st.write("Uploads validated.")
            if invoice_bytes is not None:
                st.write("Redacting and verifying invoice PII locally.")
                raw_invoice_pages = privacy.rasterize_pdf(invoice_bytes)
            st.write("Running product, warranty, and support policies.")
            result = st.session_state[AGENT_STATE_KEY].process(
                st.session_state[SESSION_STATE_KEY],
                message,
                product_image_bytes=image_bytes,
                invoice_pdf_bytes=invoice_bytes,
                issue_resolved=issue_resolved,
            )
            status.update(label="Support request processed", state="complete")
    except ModelGatewayError as error:
        st.session_state[SESSION_STATE_KEY] = session_before_request
        st.error(provider_error_message(error))
        return
    except AgentError as error:
        st.session_state[SESSION_STATE_KEY] = session_before_request
        st.error(str(error))
        return
    except (OSError, ValueError) as error:
        st.session_state[SESSION_STATE_KEY] = session_before_request
        st.error(f"The upload could not be processed: {error}")
        return

    st.session_state[RESULT_STATE_KEY] = result
    with st.chat_message("user"):
        st.write(message)
    with st.chat_message("assistant"):
        st.write(result["response"])
    _render_result(result, raw_invoice_pages=raw_invoice_pages)


def _render_result(
    result: dict[str, Any],
    *,
    raw_invoice_pages: list[Any] | None = None,
) -> None:
    status = result.get("status", "unknown")
    if status in {"rma_open", "resolved", "warranty_valid"}:
        st.success(_status_label(status))
    elif status in {"escalated", "warranty_expired"}:
        st.warning(_status_label(status))
    else:
        st.info(_status_label(status))

    product = result.get("product") or {}
    warranty = result.get("warranty")
    rma = result.get("rma")
    escalation = result.get("escalation")

    summary_columns = st.columns(3)
    summary_columns[0].metric(
        "Product",
        product.get("model_number") or "Needs confirmation",
    )
    summary_columns[1].metric(
        "Warranty",
        _warranty_label(warranty),
    )
    summary_columns[2].metric(
        "Outcome",
        _outcome_label(rma, escalation, status),
    )

    if warranty:
        with st.container(border=True):
            st.subheader("Warranty result")
            st.write(_warranty_detail(warranty))

    if rma:
        st.success(f"RMA tracking number: `{rma['rma_id']}`")

    if escalation:
        st.warning(
            "Human support ticket: "
            f"`{escalation['ticket_id']}` - "
            f"Queue: `{escalation['assigned_queue']}`"
        )

    sections = result.get("manual_sections") or []
    if sections:
        st.subheader("Manual-backed troubleshooting")
        for section in sections:
            with st.expander(section["title"], expanded=False):
                st.markdown(section["content"])

    sanitized_images = result.get("sanitized_images") or []
    if sanitized_images:
        _render_privacy_preview(
            sanitized_images,
            raw_invoice_pages=raw_invoice_pages or [],
        )


def _render_privacy_preview(
    sanitized_images: list[Any],
    *,
    raw_invoice_pages: list[Any],
) -> None:
    st.subheader("Local privacy proof")
    st.caption(
        "The raw invoice is rendered in memory for this comparison only. It "
        "is not copied into VisionCare session state, telemetry, or sent to "
        "the model."
    )

    for index, sanitized_image in enumerate(sanitized_images):
        if index < len(raw_invoice_pages):
            raw_col, safe_col = st.columns(2)
            with raw_col:
                st.markdown("**Raw invoice - local only**")
                st.image(raw_invoice_pages[index], use_container_width=True)
            with safe_col:
                st.markdown("**Redacted invoice - model input**")
                st.image(sanitized_image, use_container_width=True)
        else:
            st.markdown("**Redacted invoice - model input**")
            st.image(sanitized_image, use_container_width=True)


def _is_supported_image(data: bytes) -> bool:
    if data.startswith(
        (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")
    ):
        return True
    return data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP"


def _format_bytes(size: int) -> str:
    return f"{size // (1024 * 1024)} MB"


def _status_label(status: str) -> str:
    labels = {
        "clarification_required": "More information is required",
        "escalated": "Transferred to human support",
        "resolved": "Issue resolved",
        "rma_open": "Replacement workflow opened",
        "troubleshooting": "Troubleshooting steps ready",
        "warranty_expired": "Warranty expired",
        "warranty_not_found": "Warranty record not found",
        "warranty_valid": "Warranty is active",
    }
    return labels.get(status, status.replace("_", " ").title())


def _warranty_label(warranty: dict[str, Any] | None) -> str:
    if not warranty:
        return "Not checked"
    return {
        "valid": "Active",
        "expired": "Expired",
        "not_found": "Not found",
    }.get(warranty.get("status"), "Unknown")


def _warranty_detail(warranty: dict[str, Any]) -> str:
    status = warranty.get("status")
    expiration_date = warranty.get("expiration_date")
    if status == "valid":
        return f"Active through `{expiration_date}`."
    if status == "expired":
        return f"Expired on `{expiration_date}`. No automatic RMA was created."
    return "No matching warranty record was found."


def _outcome_label(
    rma: dict[str, Any] | None,
    escalation: dict[str, Any] | None,
    status: str,
) -> str:
    if rma:
        return str(rma.get("rma_id") or "RMA opened")
    if escalation:
        return str(escalation.get("ticket_id") or "Escalated")
    return _status_label(status)
