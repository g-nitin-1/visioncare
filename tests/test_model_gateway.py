import os
from pathlib import Path

import cv2
import numpy as np
import pytest
import requests
from dotenv import dotenv_values

from backend import model_gateway


LIVE_API_KEY_AVAILABLE = bool(
    os.getenv("MODEL_API_KEY") or dotenv_values(".env").get("MODEL_API_KEY")
)
ROUTER_FIXTURE = Path(__file__).with_name("fixtures") / "router_red_light.jpg"


def png_bytes(text="VC-R1000"):
    image = np.full((240, 420, 3), 255, dtype=np.uint8)
    cv2.putText(
        image,
        text,
        (24, 120),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.2,
        (0, 0, 0),
        2,
        cv2.LINE_AA,
    )
    success, encoded = cv2.imencode(".png", image)
    assert success
    return encoded.tobytes()


class FakeResponse:
    def __init__(self, status_code=200, body=None, text="ok"):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if isinstance(self._body, ValueError):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        if self.exc:
            raise self.exc
        return self.response


def provider_body(content="Detected router", tool_calls=None):
    message = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-test",
        "model": model_gateway.DEFAULT_MODEL,
        "choices": [{"message": message}],
    }


def test_fake_gateway_returns_expected_router_and_issue():
    gateway = model_gateway.FakeModelGateway()

    result = gateway.analyze_product_image(
        png_bytes(), "Identify the router model and issue."
    )

    assert result["status"] == "ok"
    assert result["product_name"] == "VisionCare Demo Router"
    assert result["model_number"] == "VC-R1000"
    assert result["issue_category"] == "router_connectivity"
    assert result["issue_keywords"] == ["red", "light"]


def test_fake_gateway_extracts_document_fields_from_sanitized_image():
    gateway = model_gateway.FakeModelGateway()
    image = np.full((100, 100, 3), 255, dtype=np.uint8)

    result = gateway.extract_document_fields([image], "Extract valid invoice fields.")

    assert result["fields"] == {
        "product_name": "VisionCare Demo Router",
        "model_number": "VC-R1000",
        "purchase_date": "2026-01-15",
        "serial_number": "VC1000-VALID-001",
    }


def test_fake_gateway_returns_tool_call_for_first_tool():
    gateway = model_gateway.FakeModelGateway()

    result = gateway.respond_with_tools(
        messages=[{"role": "user", "content": "Check warranty"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "check_warranty_status",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert result["tool_calls"][0]["function"]["name"] == "check_warranty_status"
    assert "VC1000-VALID-001" in result["tool_calls"][0]["function"]["arguments"]


def test_openrouter_missing_api_key_produces_useful_error():
    gateway = model_gateway.OpenRouterModelGateway(api_key="")

    with pytest.raises(model_gateway.MissingAPIKeyError, match="MODEL_API_KEY"):
        gateway.analyze_product_image(png_bytes(), "Identify model.")


def test_unsupported_file_type_is_rejected_before_provider_call():
    session = FakeSession(response=FakeResponse(body=provider_body()))
    gateway = model_gateway.OpenRouterModelGateway(
        api_key="test-key", session=session
    )

    with pytest.raises(model_gateway.UnsupportedFileTypeError):
        gateway.analyze_product_image(b"not an image", "Identify model.")

    assert session.calls == []


def test_openrouter_request_payload_uses_data_url_image():
    session = FakeSession(response=FakeResponse(body=provider_body("ok")))
    gateway = model_gateway.OpenRouterModelGateway(
        api_key="test-key", session=session
    )

    result = gateway.analyze_product_image(png_bytes(), "Identify model.")
    payload = session.calls[0]["kwargs"]["json"]
    content = payload["messages"][0]["content"]

    assert result["content"] == "ok"
    assert session.calls[0]["args"][0].endswith("/chat/completions")
    assert payload["model"] == model_gateway.DEFAULT_MODEL
    assert content[0] == {"type": "text", "text": "Identify model."}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_openrouter_extract_document_fields_encodes_numpy_images():
    session = FakeSession(response=FakeResponse(body=provider_body("fields")))
    gateway = model_gateway.OpenRouterModelGateway(
        api_key="test-key", session=session
    )
    image = np.full((100, 100, 3), 255, dtype=np.uint8)

    result = gateway.extract_document_fields([image], "Extract fields.")
    content = session.calls[0]["kwargs"]["json"]["messages"][0]["content"]

    assert result["content"] == "fields"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_provider_timeout_is_wrapped():
    session = FakeSession(exc=requests.exceptions.Timeout("slow"))
    gateway = model_gateway.OpenRouterModelGateway(
        api_key="test-key", session=session
    )

    with pytest.raises(model_gateway.ProviderTimeoutError, match="timed out"):
        gateway.analyze_product_image(png_bytes(), "Identify model.")


def test_invalid_provider_response_is_wrapped():
    session = FakeSession(response=FakeResponse(body={"choices": []}))
    gateway = model_gateway.OpenRouterModelGateway(
        api_key="test-key", session=session
    )

    with pytest.raises(model_gateway.InvalidProviderResponseError):
        gateway.analyze_product_image(png_bytes(), "Identify model.")


def test_invalid_provider_json_is_wrapped():
    session = FakeSession(response=FakeResponse(body=ValueError("bad json")))
    gateway = model_gateway.OpenRouterModelGateway(
        api_key="test-key", session=session
    )

    with pytest.raises(model_gateway.InvalidProviderResponseError):
        gateway.analyze_product_image(png_bytes(), "Identify model.")


def test_create_model_gateway_supports_fake_provider(tmp_path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("MODEL_PROVIDER=fake\n", encoding="utf-8")
    monkeypatch.delenv("MODEL_PROVIDER", raising=False)

    gateway = model_gateway.create_model_gateway(env_path=env_path)

    assert isinstance(gateway, model_gateway.FakeModelGateway)


@pytest.mark.live
@pytest.mark.skipif(
    not LIVE_API_KEY_AVAILABLE,
    reason="MODEL_API_KEY is required for live provider smoke tests",
)
def test_live_openrouter_router_image_smoke(monkeypatch):
    monkeypatch.setenv("MODEL_PROVIDER", "openrouter")
    gateway = model_gateway.create_model_gateway()

    result = gateway.analyze_product_image(
        ROUTER_FIXTURE.read_bytes(),
        "Identify any visible router model text. Return a short answer.",
    )

    assert result["status"] == "ok"
    assert result["content"]
