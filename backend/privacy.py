"""Local privacy masking pipeline for synthetic invoice documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import cv2
import fitz
import numpy as np
import pytesseract


EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
MODEL_RE = re.compile(r"\b[A-Z]{2}-[A-Z]\d{4}\b")
SERIAL_RE = re.compile(r"\bVC\d{4}-[A-Z]+-\d{3}\b")
DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
REDACTION_MARGIN_PX = 8
RASTER_DPI = 220
OCR_CONFIG = "--psm 6"
PRODUCT_FIELD_LABELS = {
    "product name": "product_name",
    "model number": "model_number",
    "purchase date": "purchase_date",
    "serial number": "serial_number",
}
PII_LABELS = ("customer name", "email", "phone", "home address")


@dataclass(frozen=True)
class OCRWord:
    text: str
    left: int
    top: int
    width: int
    height: int
    page_index: int
    block_num: int
    par_num: int
    line_num: int
    word_num: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


class NERDetector(Protocol):
    """Optional local NER interface for future PII detectors."""

    def detect(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        ...


class NoOpNERDetector:
    """Default NER hook; regex and invoice labels handle current fixtures."""

    def detect(self, lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return []


def rasterize_pdf(pdf_bytes: bytes) -> list[np.ndarray]:
    """Rasterize a PDF into RGB page images for local OCR/redaction."""
    document = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        images: list[np.ndarray] = []
        zoom = RASTER_DPI / 72
        matrix = fitz.Matrix(zoom, zoom)

        for page in document:
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height, pixmap.width, pixmap.n
            )
            images.append(image.copy())

        return images
    finally:
        document.close()


def extract_words_with_boxes(image: np.ndarray, page_index: int = 0) -> list[OCRWord]:
    """Extract OCR words and bounding boxes from one page image."""
    data = pytesseract.image_to_data(
        image,
        output_type=pytesseract.Output.DICT,
        config=OCR_CONFIG,
    )
    words: list[OCRWord] = []

    for index, text in enumerate(data["text"]):
        normalized_text = text.strip()
        if not normalized_text:
            continue

        words.append(
            OCRWord(
                text=normalized_text,
                left=int(data["left"][index]),
                top=int(data["top"][index]),
                width=int(data["width"][index]),
                height=int(data["height"][index]),
                page_index=page_index,
                block_num=int(data["block_num"][index]),
                par_num=int(data["par_num"][index]),
                line_num=int(data["line_num"][index]),
                word_num=int(data["word_num"][index]),
            )
        )

    return words


def detect_pii(
    words: list[OCRWord], ner_detector: NERDetector | None = None
) -> list[dict[str, Any]]:
    """Detect PII boxes from OCR words in the synthetic invoice format."""
    lines = _group_words_into_lines(words)
    detections: list[dict[str, Any]] = []

    for line in lines:
        line_text = line["text"]
        lower_text = line_text.lower()
        line_words = line["words"]

        detections.extend(
            _regex_value_detections(
                line_words,
                reason="email",
                pattern=EMAIL_RE,
            )
        )
        detections.extend(
            _regex_value_detections(
                line_words,
                reason="phone",
                pattern=PHONE_RE,
            )
        )

        if "customer" in lower_text and "name" in lower_text:
            detections.append(
                _line_value_detection(line_words, "customer_name", after_token="name")
            )
        elif "phone" in lower_text:
            detections.append(
                _line_value_detection(line_words, "phone", after_token="phone")
            )
        elif "email" in lower_text:
            detections.append(
                _line_value_detection(line_words, "email", after_token="email")
            )
        elif "address" in lower_text:
            detections.append(
                _line_value_detection(line_words, "home_address", after_token="address")
            )

    if ner_detector is None:
        ner_detector = NoOpNERDetector()
    detections.extend(ner_detector.detect(lines))

    return _dedupe_detections(
        detection for detection in detections if detection is not None
    )


def redact_boxes(image: np.ndarray, boxes: list[dict[str, Any]]) -> np.ndarray:
    """Return a copy of an image with matching PII boxes redacted locally."""
    redacted = image.copy()
    height, width = redacted.shape[:2]

    for box in boxes:
        left = max(0, int(box["left"]) - REDACTION_MARGIN_PX)
        top = max(0, int(box["top"]) - REDACTION_MARGIN_PX)
        right = min(width, int(box["right"]) + REDACTION_MARGIN_PX)
        bottom = min(height, int(box["bottom"]) + REDACTION_MARGIN_PX)
        cv2.rectangle(redacted, (left, top), (right, bottom), (0, 0, 0), thickness=-1)

    return redacted


def verify_redaction(
    redacted_image: np.ndarray | list[np.ndarray],
    pii_values: list[str] | None = None,
) -> dict[str, Any]:
    """Verify detected PII values no longer appear in sanitized OCR output."""
    images = redacted_image if isinstance(redacted_image, list) else [redacted_image]
    sanitized_text = "\n".join(
        pytesseract.image_to_string(image, config=OCR_CONFIG) for image in images
    )
    normalized_text = _normalize_for_compare(sanitized_text)
    remaining_values = []

    for value in pii_values or []:
        normalized_value = _normalize_for_compare(value)
        if normalized_value and normalized_value in normalized_text:
            remaining_values.append(value)

    return {
        "passed": not remaining_values,
        "remaining_values": remaining_values,
        "sanitized_text": sanitized_text,
    }


def sanitize_document(
    pdf_bytes: bytes, ner_detector: NERDetector | None = None
) -> dict[str, Any]:
    """Sanitize a PDF invoice and return redacted page images plus metadata."""
    page_images = rasterize_pdf(pdf_bytes)
    sanitized_images: list[np.ndarray] = []
    redacted_boxes: list[dict[str, Any]] = []
    all_words: list[OCRWord] = []

    for page_index, image in enumerate(page_images):
        words = extract_words_with_boxes(image, page_index=page_index)
        page_boxes = detect_pii(words, ner_detector=ner_detector)
        sanitized_images.append(redact_boxes(image, page_boxes))
        redacted_boxes.extend(page_boxes)
        all_words.extend(words)

    pii_values = [box["value_text"] for box in redacted_boxes if box.get("value_text")]
    verification = verify_redaction(sanitized_images, pii_values=pii_values)

    return {
        "sanitized_images": sanitized_images,
        "redacted_boxes": redacted_boxes,
        "extracted_fields": extract_product_fields(all_words),
        "verification": verification,
        "sanitized_text": verification["sanitized_text"],
    }


def extract_product_fields(words: list[OCRWord]) -> dict[str, str]:
    """Extract retained product fields from OCR words."""
    fields: dict[str, str] = {}
    for line in _group_words_into_lines(words):
        lower_text = line["text"].lower()
        for label, field_name in PRODUCT_FIELD_LABELS.items():
            if label not in lower_text:
                continue
            value = _line_value(line["words"], after_token=label.split()[-1])
            if field_name == "model_number":
                value = _first_regex_value(MODEL_RE, value)
            elif field_name == "serial_number":
                value = _first_regex_value(SERIAL_RE, value)
            elif field_name == "purchase_date":
                value = _first_regex_value(DATE_RE, value)
            if value:
                fields[field_name] = value
    return fields


def _group_words_into_lines(words: list[OCRWord]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int, int, int], list[OCRWord]] = {}
    for word in words:
        key = (word.page_index, word.block_num, word.par_num, word.line_num)
        grouped.setdefault(key, []).append(word)

    lines: list[dict[str, Any]] = []
    for key in sorted(grouped):
        line_words = sorted(grouped[key], key=lambda item: item.word_num)
        lines.append(
            {
                "key": key,
                "words": line_words,
                "text": " ".join(word.text for word in line_words),
            }
        )
    return lines


def _regex_value_detections(
    words: list[OCRWord], reason: str, pattern: re.Pattern[str]
) -> list[dict[str, Any]]:
    detections: list[dict[str, Any]] = []
    for word in words:
        if pattern.search(word.text):
            detections.append(_box_from_words([word], reason=reason, value_text=word.text))
    return detections


def _line_value_detection(
    words: list[OCRWord], reason: str, after_token: str
) -> dict[str, Any] | None:
    value_words = _words_after_label(words, after_token)
    if not value_words:
        return None
    return _box_from_words(
        value_words,
        reason=reason,
        value_text=" ".join(word.text for word in value_words),
    )


def _line_value(words: list[OCRWord], after_token: str) -> str:
    return " ".join(word.text for word in _words_after_label(words, after_token))


def _words_after_label(words: list[OCRWord], after_token: str) -> list[OCRWord]:
    normalized_after_token = _normalize_token(after_token)
    for index, word in enumerate(words):
        if _normalize_token(word.text).rstrip(":") == normalized_after_token:
            return words[index + 1 :]
    return []


def _box_from_words(
    words: list[OCRWord], reason: str, value_text: str
) -> dict[str, Any]:
    return {
        "page_index": words[0].page_index,
        "left": min(word.left for word in words),
        "top": min(word.top for word in words),
        "right": max(word.right for word in words),
        "bottom": max(word.bottom for word in words),
        "reason": reason,
        "value_text": value_text,
    }


def _dedupe_detections(detections: Any) -> list[dict[str, Any]]:
    unique: dict[tuple[Any, ...], dict[str, Any]] = {}
    for detection in detections:
        key = (
            detection["page_index"],
            detection["left"],
            detection["top"],
            detection["right"],
            detection["bottom"],
            detection["reason"],
        )
        unique[key] = detection
    return list(unique.values())


def _first_regex_value(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(0) if match else text.strip()


def _normalize_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _normalize_for_compare(text: str) -> str:
    return re.sub(r"[^a-z0-9@.]+", "", text.lower())
