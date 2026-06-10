"""Run the deterministic VisionCare demo flow without launching a browser."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend import analytics  # noqa: E402
from backend.agent import SupportAgent  # noqa: E402
from backend.model_gateway import FakeModelGateway  # noqa: E402
from scripts.reset_demo import (  # noqa: E402
    DEFAULT_DB_PATH,
    DEFAULT_LOG_PATH,
    DEFAULT_SEED_PATH,
    reset_demo_state,
)


ROUTER_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "router_red_light.jpg"
VALID_INVOICE = PROJECT_ROOT / "tests" / "fixtures" / "valid_invoice.pdf"


def run_rehearsal(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    seed_path: str | Path = DEFAULT_SEED_PATH,
    log_path: str | Path = DEFAULT_LOG_PATH,
) -> dict[str, Any]:
    """Reset and execute the scripted router-to-RMA demonstration."""
    reset_demo_state(
        db_path=db_path,
        seed_path=seed_path,
        log_path=log_path,
    )
    support_agent = SupportAgent(
        gateway=FakeModelGateway(),
        db_path=db_path,
        log_path=log_path,
    )
    session = support_agent.create_session(
        "demo-rehearsal-user",
        "demo-rehearsal-session",
    )
    router_bytes = ROUTER_FIXTURE.read_bytes()

    troubleshooting = support_agent.process(
        session,
        "My VC-R1000 router has a red status light.",
        product_image_bytes=router_bytes,
    )
    rma_result = support_agent.process(
        session,
        (
            "The red light remains after the power cycle and cable checks. "
            "Please check the invoice and replace the router."
        ),
        product_image_bytes=router_bytes,
        invoice_pdf_bytes=VALID_INVOICE.read_bytes(),
        issue_resolved=False,
    )
    metrics = analytics.get_dashboard_metrics(log_path)

    _verify_rehearsal(troubleshooting, rma_result, metrics)
    return {
        "troubleshooting": troubleshooting,
        "rma": rma_result,
        "metrics": metrics,
    }


def main() -> int:
    result = run_rehearsal()
    troubleshooting_titles = [
        section["title"]
        for section in result["troubleshooting"]["manual_sections"]
    ]
    print("Demo rehearsal passed.")
    print(f"Manual sections: {', '.join(troubleshooting_titles)}")
    print(f"Warranty: {result['rma']['warranty']['status']}")
    print(f"RMA: {result['rma']['rma']['rma_id']}")
    print(
        "Dashboard: "
        f"{result['metrics']['total_completed_sessions']} session, "
        f"{result['metrics']['open_rma_count']} open RMA, "
        f"{result['metrics']['warranty_claim_count']} warranty claim"
    )
    print("Launch Streamlit now to inspect the populated admin dashboard.")
    return 0


def _verify_rehearsal(
    troubleshooting: dict[str, Any],
    rma_result: dict[str, Any],
    metrics: dict[str, Any],
) -> None:
    if troubleshooting["status"] != "troubleshooting":
        raise RuntimeError("rehearsal did not return troubleshooting steps")
    if not troubleshooting["manual_sections"]:
        raise RuntimeError("rehearsal returned no manual sections")
    if rma_result["status"] != "rma_open":
        raise RuntimeError("rehearsal did not create an eligible RMA")
    if rma_result["rma"]["rma_id"] != "RMA-0001":
        raise RuntimeError("rehearsal did not start from deterministic RMA state")

    expected_metrics = {
        "total_completed_sessions": 1,
        "active_users": 1,
        "resolution_rate": 1.0,
        "warranty_claim_count": 1,
        "open_rma_count": 1,
        "human_escalation_count": 0,
    }
    for field_name, expected_value in expected_metrics.items():
        if metrics[field_name] != expected_value:
            raise RuntimeError(
                f"unexpected dashboard metric {field_name}: "
                f"{metrics[field_name]} != {expected_value}"
            )


if __name__ == "__main__":
    raise SystemExit(main())
