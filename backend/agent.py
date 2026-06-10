"""Deterministic support workflow orchestration for VisionCare."""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend import logger, manual_loader, privacy, tools
from backend.model_gateway import ModelGateway, create_model_gateway


SCHEMA_VERSION = 1
DEFAULT_CONFIDENCE_THRESHOLD = 0.65
MODEL_NUMBER_RE = re.compile(r"\bVC[\s-]*R[\s-]*1000\b", re.IGNORECASE)
SERIAL_NUMBER_RE = re.compile(
    r"\bVC1000[\s-]*(?:VALID|EXPIRED)[\s-]*001\b", re.IGNORECASE
)
HIGHLY_NEGATIVE_TERMS = {
    "angry",
    "awful",
    "furious",
    "hate",
    "ridiculous",
    "terrible",
    "unacceptable",
    "worst",
}
UNRESOLVED_TERMS = (
    "after troubleshooting",
    "did not work",
    "didn't work",
    "keeps failing",
    "remains",
    "replacement",
    "request rma",
    "still broken",
    "still failing",
    "still red",
)
HUMAN_REQUEST_TERMS = (
    "human agent",
    "human support",
    "speak to a human",
    "talk to a human",
)


class AgentError(RuntimeError):
    """Base error for support workflow failures."""


class PrivacyVerificationError(AgentError):
    """Raised when a document cannot be verified as sanitized."""


@dataclass
class AgentSessionState:
    """Mutable state preserved across support turns."""

    user_id: str
    session_id: str = field(default_factory=lambda: f"session_{uuid.uuid4().hex}")
    anonymous_user_id: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    product_id: str | None = None
    serial_number: str | None = None
    intent: str = "troubleshooting"
    diagnosis_attempts: int = 0
    sentiment: str = "neutral"
    resolved: bool = False
    escalated: bool = False
    rma_id: str | None = None
    escalation_ticket_id: str | None = None
    started_at: float = field(default_factory=time.monotonic, repr=False)
    summary_logged: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        self.user_id = _required_text("user_id", self.user_id)
        self.session_id = _required_text("session_id", self.session_id)
        if not self.anonymous_user_id:
            self.anonymous_user_id = logger.make_anonymous_user_id(self.user_id)


SessionState = AgentSessionState
DocumentSanitizer = Callable[[bytes], dict[str, Any]]


class SupportAgent:
    """Connect model observations to deterministic support policies and tools."""

    def __init__(
        self,
        gateway: ModelGateway | None = None,
        *,
        db_path: str | Path | None = None,
        log_path: str | Path | None = None,
        manifest_path: str | Path | None = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        document_sanitizer: DocumentSanitizer = privacy.sanitize_document,
    ) -> None:
        if not 0 <= confidence_threshold <= 1:
            raise ValueError("confidence_threshold must be between 0 and 1")

        self.gateway = gateway if gateway is not None else create_model_gateway()
        self.db_path = Path(db_path) if db_path is not None else None
        self.log_path = Path(log_path) if log_path is not None else None
        self.manifest_path = (
            Path(manifest_path) if manifest_path is not None else None
        )
        self.confidence_threshold = confidence_threshold
        self.document_sanitizer = document_sanitizer

    def create_session(
        self, user_id: str, session_id: str | None = None
    ) -> AgentSessionState:
        """Create a support session with an opaque telemetry identifier."""
        return AgentSessionState(
            user_id=user_id,
            session_id=session_id or f"session_{uuid.uuid4().hex}",
        )

    def process(
        self,
        session: AgentSessionState,
        message: str,
        *,
        product_image_bytes: bytes | None = None,
        invoice_pdf_bytes: bytes | None = None,
        issue_resolved: bool | None = None,
        sentiment: str | None = None,
    ) -> dict[str, Any]:
        """Run one support turn and return structured data for the future UI."""
        started_at = time.monotonic()
        user_message = _required_text("message", message)
        session.messages.append({"role": "user", "content": user_message})
        session.sentiment = _detect_sentiment(user_message, sentiment)

        model_results: list[dict[str, Any]] = []
        permitted_fields: dict[str, str] = {}
        sanitized_images: list[Any] = []

        if invoice_pdf_bytes is not None:
            document_result = self.document_sanitizer(invoice_pdf_bytes)
            verification = document_result.get("verification", {})
            if verification.get("passed") is not True:
                raise PrivacyVerificationError(
                    "document redaction verification failed; no document data "
                    "was sent to the model"
                )

            sanitized_images = list(document_result.get("sanitized_images") or [])
            local_fields = _normalize_fields(
                document_result.get("extracted_fields")
            )
            document_model_result = self.gateway.extract_document_fields(
                sanitized_images,
                _document_prompt(local_fields),
            )
            model_results.append(document_model_result)
            model_fields = _extract_fields_from_model(document_model_result)
            permitted_fields = {**model_fields, **local_fields}

        image_result: dict[str, Any] = {}
        if product_image_bytes is not None:
            image_result = self.gateway.analyze_product_image(
                product_image_bytes,
                (
                    "Identify the product model number and visible hardware "
                    "issue. Return concise JSON when possible."
                ),
            )
            model_results.append(image_result)

        model_number = _first_nonempty(
            permitted_fields.get("model_number"),
            image_result.get("model_number"),
            _extract_model_number(user_message),
            _extract_model_number(_model_content(image_result)),
        )
        serial_number = _first_nonempty(
            permitted_fields.get("serial_number"),
            _extract_serial_number(user_message),
        )
        if serial_number:
            session.serial_number = serial_number
        session.intent = _determine_intent(
            user_message,
            has_invoice=invoice_pdf_bytes is not None,
            has_serial=session.serial_number is not None,
        )

        product_result = manual_loader.identify_product(
            product_name=_first_nonempty(
                permitted_fields.get("product_name"),
                image_result.get("product_name"),
            ),
            model_number=model_number,
            manifest_path=self.manifest_path,
        )

        issue_category = _first_nonempty(
            image_result.get("issue_category"),
            "router_connectivity" if _mentions_router_issue(user_message) else None,
            "unknown_error",
        )
        issue_keywords = _issue_keywords(user_message, image_result)
        confidence = _minimum_confidence(model_results)
        escalation_reason = _policy_escalation_reason(
            session.sentiment,
            confidence,
            self.confidence_threshold,
            user_message,
        )

        if product_result["status"] == "clarification_required":
            session.diagnosis_attempts += 1
            if session.diagnosis_attempts >= 2:
                escalation_reason = escalation_reason or "diagnosis_attempts_exhausted"

            if escalation_reason:
                return self._finish_escalation(
                    session,
                    reason=escalation_reason,
                    issue_category=issue_category,
                    model_results=model_results,
                    started_at=started_at,
                    sanitized_images=sanitized_images,
                    product=product_result,
                )

            response = product_result["message"]
            session.messages.append({"role": "assistant", "content": response})
            return _result(
                status="clarification_required",
                response=response,
                session=session,
                product=product_result,
                manual_sections=[],
                warranty=None,
                rma=None,
                escalation=None,
                sanitized_images=sanitized_images,
            )

        if product_result["status"] != "identified":
            raise AgentError(
                f"unsupported identify_product status: {product_result['status']}"
            )

        session.product_id = product_result["product_id"]
        manual_result = manual_loader.load_manual_sections(
            session.product_id,
            issue_keywords,
            manifest_path=self.manifest_path,
        )
        if manual_result["status"] == "clarification_required":
            session.diagnosis_attempts += 1
        elif manual_result["status"] != "loaded":
            raise AgentError(
                f"unsupported load_manual_sections status: {manual_result['status']}"
            )
        elif not manual_result["sections"]:
            session.diagnosis_attempts += 1

        if session.diagnosis_attempts >= 2:
            escalation_reason = escalation_reason or "diagnosis_attempts_exhausted"
        if escalation_reason:
            return self._finish_escalation(
                session,
                reason=escalation_reason,
                issue_category=issue_category,
                model_results=model_results,
                started_at=started_at,
                sanitized_images=sanitized_images,
                product=product_result,
                manual_sections=manual_result.get("sections", []),
            )

        unresolved = _is_unresolved(user_message, issue_resolved)
        rma_requested = _requests_rma(user_message) or unresolved
        diagnosis_required = session.intent == "troubleshooting" or rma_requested
        if diagnosis_required and not manual_result.get("sections"):
            response = (
                "I identified the router, but I need more detail about the "
                "visible light or connectivity problem before deciding the "
                "next step."
            )
            session.messages.append({"role": "assistant", "content": response})
            return _result(
                status="clarification_required",
                response=response,
                session=session,
                product=product_result,
                manual_sections=[],
                warranty=None,
                rma=None,
                escalation=None,
                sanitized_images=sanitized_images,
            )

        warranty_result: dict[str, Any] | None = None
        rma_result: dict[str, Any] | None = None
        if session.serial_number:
            warranty_result = tools.check_warranty_status(
                session.serial_number,
                db_path=self.db_path,
            )
            self._log_tool_call(session, "check_warranty_status", warranty_result)

        if rma_requested and warranty_result and warranty_result["status"] == "valid":
            if warranty_result["product_id"] != session.product_id:
                response = (
                    "The invoice serial number belongs to a different product. "
                    "Please verify the model and serial number."
                )
            else:
                rma_result = tools.initiate_rma_process(
                    user_id=session.user_id,
                    product_id=session.product_id,
                    defect_description=_defect_description(
                        user_message, issue_category
                    ),
                    idempotency_key=f"{session.session_id}:rma",
                    db_path=self.db_path,
                )
                self._log_tool_call(session, "initiate_rma_process", rma_result)
                session.rma_id = rma_result.get("rma_id")
                session.resolved = session.rma_id is not None
                response = _rma_response(session.rma_id, warranty_result)
        elif rma_requested and warranty_result and warranty_result["status"] == "expired":
            session.resolved = False
            response = _expired_warranty_response(warranty_result)
        elif rma_requested and warranty_result and warranty_result["status"] == "not_found":
            session.resolved = False
            response = (
                "I could not find that serial number in the warranty system. "
                "Please verify it or ask for human support."
            )
        elif rma_requested and warranty_result is None:
            session.resolved = False
            response = (
                "I need the product serial number or a sanitized invoice before "
                "I can check RMA eligibility."
            )
        elif warranty_result is not None:
            session.resolved = warranty_result["status"] in {"valid", "expired"}
            response = _warranty_response(warranty_result)
        else:
            session.resolved = bool(issue_resolved)
            response = _troubleshooting_response(
                manual_result.get("sections", []),
                issue_resolved=issue_resolved,
            )

        session.messages.append({"role": "assistant", "content": response})
        if _is_terminal_result(session, warranty_result, rma_requested):
            self._log_summary_once(
                session,
                issue_category=issue_category,
                model_results=model_results,
                latency_ms=_elapsed_ms(started_at),
            )

        status = _result_status(session, warranty_result, rma_result)
        return _result(
            status=status,
            response=response,
            session=session,
            product=product_result,
            manual_sections=manual_result.get("sections", []),
            warranty=warranty_result,
            rma=rma_result,
            escalation=None,
            sanitized_images=sanitized_images,
        )

    def _finish_escalation(
        self,
        session: AgentSessionState,
        *,
        reason: str,
        issue_category: str,
        model_results: list[dict[str, Any]],
        started_at: float,
        sanitized_images: list[Any],
        product: dict[str, Any],
        manual_sections: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        session.intent = "human_escalation"
        escalation_result = tools.escalate_to_human(
            priority_level=(
                "High" if reason in {"highly_negative_sentiment", "low_confidence"} else "Low"
            ),
            chat_history_summary=_safe_chat_summary(
                session, reason=reason, issue_category=issue_category
            ),
            idempotency_key=f"{session.session_id}:escalation",
            db_path=self.db_path,
        )
        self._log_tool_call(session, "escalate_to_human", escalation_result)
        session.escalated = True
        session.resolved = False
        session.escalation_ticket_id = escalation_result["ticket_id"]
        response = (
            "I could not complete this safely without human review. "
            f"Support ticket {session.escalation_ticket_id} has been opened."
        )
        session.messages.append({"role": "assistant", "content": response})
        self._log_summary_once(
            session,
            issue_category=issue_category,
            model_results=model_results,
            latency_ms=_elapsed_ms(started_at),
        )
        return _result(
            status="escalated",
            response=response,
            session=session,
            product=product,
            manual_sections=manual_sections or [],
            warranty=None,
            rma=None,
            escalation=escalation_result,
            sanitized_images=sanitized_images,
        )

    def _log_tool_call(
        self,
        session: AgentSessionState,
        tool_name: str,
        tool_result: dict[str, Any],
    ) -> None:
        logger.append_event(
            {
                "schema_version": SCHEMA_VERSION,
                "session_id": session.session_id,
                "event_type": "tool_call",
                "tool_name": tool_name,
                "tool_status": str(tool_result.get("status", "unknown")),
            },
            log_path=self.log_path,
        )

    def _log_summary_once(
        self,
        session: AgentSessionState,
        *,
        issue_category: str,
        model_results: list[dict[str, Any]],
        latency_ms: int,
    ) -> None:
        if session.summary_logged:
            return

        logger.append_event(
            {
                "schema_version": SCHEMA_VERSION,
                "session_id": session.session_id,
                "event_type": "session_summary",
                "intent": session.intent,
                "anonymous_user_id": session.anonymous_user_id,
                "issue_category": issue_category,
                "hardware_issue_detected": issue_category != "unknown_error",
                "sentiment": session.sentiment,
                "resolved": session.resolved,
                "escalated": session.escalated,
                "rma_id": session.rma_id,
                "model": _model_name(self.gateway, model_results),
                "latency_ms": latency_ms,
                "resolution_time_sec": max(
                    0.0, round(time.monotonic() - session.started_at, 3)
                ),
            },
            log_path=self.log_path,
        )
        session.summary_logged = True


def run_support_workflow(
    *,
    user_id: str,
    message: str,
    product_image_bytes: bytes | None = None,
    invoice_pdf_bytes: bytes | None = None,
    session_id: str | None = None,
    gateway: ModelGateway | None = None,
    db_path: str | Path | None = None,
    log_path: str | Path | None = None,
    issue_resolved: bool | None = None,
    sentiment: str | None = None,
) -> dict[str, Any]:
    """Convenience entry point for a one-turn support workflow."""
    agent = SupportAgent(gateway=gateway, db_path=db_path, log_path=log_path)
    session = agent.create_session(user_id=user_id, session_id=session_id)
    return agent.process(
        session,
        message,
        product_image_bytes=product_image_bytes,
        invoice_pdf_bytes=invoice_pdf_bytes,
        issue_resolved=issue_resolved,
        sentiment=sentiment,
    )


def _document_prompt(local_fields: dict[str, str]) -> str:
    field_names = ", ".join(sorted(local_fields)) or "none"
    return (
        "Read only the sanitized document. Return JSON with product_name, "
        "model_number, purchase_date, and serial_number when visible. "
        f"Local OCR retained these permitted field names: {field_names}."
    )


def _extract_fields_from_model(result: dict[str, Any]) -> dict[str, str]:
    direct_fields = _normalize_fields(result.get("fields"))
    if direct_fields:
        return direct_fields

    payload = _json_content(result.get("content"))
    if isinstance(payload, dict):
        nested = payload.get("fields")
        return _normalize_fields(nested if isinstance(nested, dict) else payload)
    return {}


def _json_content(content: Any) -> Any:
    if not isinstance(content, str):
        return None
    candidate = content.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"\s*```$", "", candidate)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _normalize_fields(fields: Any) -> dict[str, str]:
    if not isinstance(fields, dict):
        return {}
    permitted = {"product_name", "model_number", "purchase_date", "serial_number"}
    return {
        key: str(value).strip()
        for key, value in fields.items()
        if key in permitted and value is not None and str(value).strip()
    }


def _model_content(result: dict[str, Any]) -> str:
    return str(result.get("content") or "")


def _extract_model_number(text: str) -> str | None:
    match = MODEL_NUMBER_RE.search(text)
    return "VC-R1000" if match else None


def _extract_serial_number(text: str) -> str | None:
    match = SERIAL_NUMBER_RE.search(text)
    if not match:
        return None
    return re.sub(r"[\s-]+", "-", match.group(0).upper())


def _first_nonempty(*values: Any) -> Any:
    for value in values:
        if value is not None and str(value).strip():
            return value
    return None


def _detect_sentiment(message: str, explicit_sentiment: str | None) -> str:
    if explicit_sentiment is not None:
        normalized = explicit_sentiment.strip().lower().replace(" ", "_")
        if normalized in {"highly_negative", "angry", "furious"}:
            return "highly_negative"
        if normalized in {"negative", "frustrated"}:
            return "frustrated"
        if normalized in {"positive", "neutral"}:
            return normalized
        raise ValueError(f"unsupported sentiment: {explicit_sentiment}")

    terms = set(re.findall(r"[a-z]+", message.lower()))
    if terms.intersection(HIGHLY_NEGATIVE_TERMS):
        return "highly_negative"
    if terms.intersection({"annoyed", "frustrated", "upset"}):
        return "frustrated"
    return "neutral"


def _policy_escalation_reason(
    sentiment: str,
    confidence: float | None,
    confidence_threshold: float,
    message: str,
) -> str | None:
    if sentiment == "highly_negative":
        return "highly_negative_sentiment"
    if confidence is not None and confidence < confidence_threshold:
        return "low_confidence"
    if _contains_phrase(message, HUMAN_REQUEST_TERMS):
        return "human_requested"
    return None


def _confidence(result: dict[str, Any]) -> float | None:
    value = result.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0.0, min(1.0, float(value)))


def _minimum_confidence(
    model_results: list[dict[str, Any]]
) -> float | None:
    values = [
        confidence
        for result in model_results
        if (confidence := _confidence(result)) is not None
    ]
    return min(values) if values else None


def _issue_keywords(message: str, image_result: dict[str, Any]) -> list[str]:
    model_keywords = image_result.get("issue_keywords")
    if isinstance(model_keywords, (list, tuple)):
        normalized = [str(item).strip() for item in model_keywords if str(item).strip()]
        if normalized:
            return normalized

    message_terms = re.findall(r"[a-z0-9]+", message.lower())
    useful = [
        term
        for term in message_terms
        if term
        in {
            "cable",
            "connectivity",
            "factory",
            "light",
            "power",
            "red",
            "reset",
            "router",
            "status",
        }
    ]
    return useful or message_terms


def _mentions_router_issue(message: str) -> bool:
    terms = set(re.findall(r"[a-z]+", message.lower()))
    return bool(
        terms.intersection(
            {"cable", "connectivity", "internet", "light", "power", "red", "router"}
        )
    )


def _determine_intent(message: str, *, has_invoice: bool, has_serial: bool) -> str:
    lowered = message.lower()
    if has_invoice or has_serial or any(
        term in lowered for term in ("warranty", "invoice", "rma", "replacement")
    ):
        return "warranty_check"
    return "troubleshooting"


def _is_unresolved(message: str, explicit_value: bool | None) -> bool:
    if explicit_value is not None:
        return not explicit_value
    return _contains_phrase(message, UNRESOLVED_TERMS)


def _requests_rma(message: str) -> bool:
    lowered = message.lower()
    return any(term in lowered for term in ("rma", "replacement", "replace it"))


def _contains_phrase(message: str, phrases: tuple[str, ...]) -> bool:
    lowered = message.lower()
    return any(phrase in lowered for phrase in phrases)


def _defect_description(message: str, issue_category: str) -> str:
    compact = " ".join(message.split())
    if len(compact) > 240:
        compact = f"{compact[:237]}..."
    return compact or issue_category


def _safe_chat_summary(
    session: AgentSessionState, *, reason: str, issue_category: str
) -> str:
    return (
        f"Session {session.session_id} requires human review. "
        f"Reason: {reason}. Issue category: {issue_category}. "
        f"Diagnosis attempts: {session.diagnosis_attempts}."
    )


def _rma_response(
    rma_id: str | None, warranty_result: dict[str, Any]
) -> str:
    return (
        f"Your warranty is valid through {warranty_result['expiration_date']}. "
        f"I opened RMA {rma_id} for the unresolved router defect."
    )


def _expired_warranty_response(warranty_result: dict[str, Any]) -> str:
    return (
        f"The warranty expired on {warranty_result['expiration_date']}. "
        "I did not open an automatic RMA. I can connect you with human support "
        "to discuss paid repair or replacement options."
    )


def _warranty_response(warranty_result: dict[str, Any]) -> str:
    if warranty_result["status"] == "valid":
        return (
            f"The warranty is valid through {warranty_result['expiration_date']}."
        )
    if warranty_result["status"] == "expired":
        return _expired_warranty_response(warranty_result)
    return (
        "I could not find that serial number in the warranty system. "
        "Please verify it or ask for human support."
    )


def _troubleshooting_response(
    sections: list[dict[str, str]], issue_resolved: bool | None
) -> str:
    if issue_resolved is True:
        return "The reported router issue is resolved."
    if not sections:
        return (
            "I identified the router, but I need more detail about the visible "
            "light or connectivity problem before suggesting a procedure."
        )

    steps = "\n\n".join(
        f"{section['title']}:\n{section['content']}" for section in sections
    )
    return f"Try these manual-backed steps:\n\n{steps}"


def _result_status(
    session: AgentSessionState,
    warranty_result: dict[str, Any] | None,
    rma_result: dict[str, Any] | None,
) -> str:
    if rma_result is not None:
        return "rma_open"
    if warranty_result is not None:
        return f"warranty_{warranty_result['status']}"
    return "resolved" if session.resolved else "troubleshooting"


def _is_terminal_result(
    session: AgentSessionState,
    warranty_result: dict[str, Any] | None,
    rma_requested: bool,
) -> bool:
    if session.resolved or session.escalated:
        return True
    return bool(
        rma_requested
        and warranty_result is not None
        and warranty_result["status"] == "expired"
    )


def _result(
    *,
    status: str,
    response: str,
    session: AgentSessionState,
    product: dict[str, Any],
    manual_sections: list[dict[str, str]],
    warranty: dict[str, Any] | None,
    rma: dict[str, Any] | None,
    escalation: dict[str, Any] | None,
    sanitized_images: list[Any],
) -> dict[str, Any]:
    return {
        "status": status,
        "response": response,
        "session": session,
        "product": product,
        "manual_sections": manual_sections,
        "warranty": warranty,
        "rma": rma,
        "escalation": escalation,
        "sanitized_images": sanitized_images,
    }


def _model_name(
    gateway: ModelGateway, model_results: list[dict[str, Any]]
) -> str:
    for result in reversed(model_results):
        model = result.get("model")
        if model:
            return str(model)
    return str(getattr(gateway, "model", gateway.__class__.__name__))


def _elapsed_ms(started_at: float) -> int:
    return max(0, round((time.monotonic() - started_at) * 1000))


def _required_text(field_name: str, value: Any) -> str:
    if value is None:
        raise ValueError(f"{field_name} is required")
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized
