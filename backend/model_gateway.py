"""Model gateway adapters for VisionCare."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
import requests
from dotenv import load_dotenv


DEFAULT_PROVIDER = "openrouter"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openrouter/free"
DEFAULT_TIMEOUT_SEC = 30
SUPPORTED_IMAGE_MIME_TYPES = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
}


class ModelGatewayError(RuntimeError):
    """Base error for model gateway failures."""


class MissingAPIKeyError(ModelGatewayError):
    """Raised when a real provider call is attempted without an API key."""


class UnsupportedFileTypeError(ModelGatewayError):
    """Raised when image bytes are not a supported image format."""


class ProviderTimeoutError(ModelGatewayError):
    """Raised when the upstream provider times out."""


class InvalidProviderResponseError(ModelGatewayError):
    """Raised when the provider response shape cannot be parsed."""


class ModelGateway(Protocol):
    """Replaceable model gateway interface used by the future agent."""

    def analyze_product_image(self, image_bytes: bytes, prompt: str) -> dict[str, Any]:
        ...

    def extract_document_fields(
        self, sanitized_images: list[Any], prompt: str
    ) -> dict[str, Any]:
        ...

    def respond_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        ...


class OpenRouterModelGateway:
    """OpenRouter chat-completions adapter."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout_sec: int = DEFAULT_TIMEOUT_SEC,
        session: Any | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec
        self.session = session or requests.Session()

    def analyze_product_image(self, image_bytes: bytes, prompt: str) -> dict[str, Any]:
        data_url = _image_bytes_to_data_url(image_bytes)
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "temperature": 0,
        }
        return self._chat_completion(payload)

    def extract_document_fields(
        self, sanitized_images: list[Any], prompt: str
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for image in sanitized_images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _image_bytes_to_data_url(_to_png_bytes(image))},
                }
            )

        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
        }
        return self._chat_completion(payload)

    def respond_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": 0,
        }
        return self._chat_completion(payload)

    def _chat_completion(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise MissingAPIKeyError("MODEL_API_KEY is required for OpenRouter calls")

        try:
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=self.timeout_sec,
            )
        except requests.exceptions.Timeout as exc:
            raise ProviderTimeoutError("OpenRouter request timed out") from exc
        except requests.RequestException as exc:
            raise ModelGatewayError(f"OpenRouter request failed: {exc}") from exc

        if response.status_code >= 400:
            raise ModelGatewayError(
                f"OpenRouter request failed with HTTP {response.status_code}: "
                f"{response.text}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise InvalidProviderResponseError("provider returned invalid JSON") from exc

        return _parse_chat_completion(body)


class FakeModelGateway:
    """Deterministic offline gateway used by tests and local development."""

    model = "fake/visioncare-demo"

    def analyze_product_image(self, image_bytes: bytes, prompt: str) -> dict[str, Any]:
        _image_bytes_to_data_url(image_bytes)
        return {
            "status": "ok",
            "model": self.model,
            "content": "Detected VisionCare Demo Router VC-R1000 with red status light.",
            "product_name": "VisionCare Demo Router",
            "model_number": "VC-R1000",
            "issue_category": "router_connectivity",
            "issue_keywords": ["red", "light"],
            "confidence": 0.99,
            "raw": {"provider": "fake"},
        }

    def extract_document_fields(
        self, sanitized_images: list[Any], prompt: str
    ) -> dict[str, Any]:
        for image in sanitized_images:
            _to_png_bytes(image)

        is_expired = "expired" in prompt.lower()
        serial_number = "VC1000-EXPIRED-001" if is_expired else "VC1000-VALID-001"
        purchase_date = "2023-01-15" if is_expired else "2026-01-15"
        return {
            "status": "ok",
            "model": self.model,
            "content": "Extracted product fields from sanitized invoice.",
            "fields": {
                "product_name": "VisionCare Demo Router",
                "model_number": "VC-R1000",
                "purchase_date": purchase_date,
                "serial_number": serial_number,
            },
            "raw": {"provider": "fake"},
        }

    def respond_with_tools(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> dict[str, Any]:
        tool_name = _first_tool_name(tools)
        tool_calls = []
        if tool_name:
            tool_calls.append(
                {
                    "id": "fake-tool-call-1",
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(
                            {"serial_number": "VC1000-VALID-001"},
                            sort_keys=True,
                        ),
                    },
                }
            )

        return {
            "status": "ok",
            "model": self.model,
            "content": "Use the available support tool for the next step.",
            "tool_calls": tool_calls,
            "raw": {"provider": "fake"},
        }


def create_model_gateway(env_path: str | Path | None = None) -> ModelGateway:
    """Create a model gateway from environment variables."""
    if env_path is not None:
        load_dotenv(env_path)
    else:
        load_dotenv()

    provider = os.getenv("MODEL_PROVIDER", DEFAULT_PROVIDER).strip().lower()
    if provider == "fake":
        return FakeModelGateway()
    if provider != DEFAULT_PROVIDER:
        raise ModelGatewayError(f"unsupported MODEL_PROVIDER: {provider}")

    return OpenRouterModelGateway(
        api_key=os.getenv("MODEL_API_KEY"),
        base_url=os.getenv("MODEL_BASE_URL", DEFAULT_BASE_URL),
        model=os.getenv("PRIMARY_MODEL", DEFAULT_MODEL),
    )


def _parse_chat_completion(body: dict[str, Any]) -> dict[str, Any]:
    try:
        choice = body["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise InvalidProviderResponseError(
            "provider response missing choices[0].message"
        ) from exc

    content = _message_content_to_text(message.get("content"))
    tool_calls = message.get("tool_calls") or []
    return {
        "status": "ok",
        "model": body.get("model"),
        "content": content,
        "tool_calls": tool_calls,
        "raw": body,
    }


def _message_content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text_parts.append(str(item.get("text", "")))
        return "\n".join(part for part in text_parts if part)
    raise InvalidProviderResponseError("provider message content has invalid type")


def _image_bytes_to_data_url(image_bytes: bytes) -> str:
    mime_type = _detect_image_mime_type(image_bytes)
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _detect_image_mime_type(image_bytes: bytes) -> str:
    if not image_bytes:
        raise UnsupportedFileTypeError("image bytes are empty")

    for signature, mime_type in SUPPORTED_IMAGE_MIME_TYPES.items():
        if image_bytes.startswith(signature):
            return mime_type
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "image/webp"

    raise UnsupportedFileTypeError(
        "unsupported image type; expected PNG, JPEG, WebP, or GIF bytes"
    )


def _to_png_bytes(image: Any) -> bytes:
    if isinstance(image, bytes):
        _detect_image_mime_type(image)
        return image
    if not isinstance(image, np.ndarray):
        raise UnsupportedFileTypeError("sanitized image must be bytes or a numpy array")

    success, encoded = cv2.imencode(".png", image)
    if not success:
        raise UnsupportedFileTypeError("could not encode sanitized image as PNG")
    return encoded.tobytes()


def _first_tool_name(tools: list[dict[str, Any]]) -> str | None:
    if not tools:
        return None
    function = tools[0].get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    return str(name) if name else None
