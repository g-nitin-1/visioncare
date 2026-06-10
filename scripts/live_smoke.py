"""Opt-in live model smoke test using the committed router fixture."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.model_gateway import (  # noqa: E402
    FakeModelGateway,
    ModelGateway,
    create_model_gateway,
)


ROUTER_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "router_red_light.jpg"
SMOKE_PROMPT = (
    "Identify the router model text and visible status-light color. "
    "Return a short answer."
)


def run_smoke(
    gateway: ModelGateway,
    image_path: str | Path = ROUTER_FIXTURE,
) -> dict[str, Any]:
    """Run one image request and validate the minimum provider response."""
    fixture_path = Path(image_path)
    result = gateway.analyze_product_image(
        fixture_path.read_bytes(),
        SMOKE_PROMPT,
    )
    if result.get("status") != "ok" or not str(result.get("content", "")).strip():
        raise RuntimeError("live model smoke test returned an empty response")
    return result


def main() -> int:
    gateway = create_model_gateway()
    if isinstance(gateway, FakeModelGateway):
        raise SystemExit(
            "Live smoke requires MODEL_PROVIDER=openrouter and MODEL_API_KEY."
        )

    result = run_smoke(gateway)
    print(f"Live smoke passed with model: {result.get('model') or 'unknown'}")
    print(str(result["content"]).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
