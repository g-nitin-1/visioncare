import hashlib
import re
from pathlib import Path

import numpy as np

from backend import privacy


FIXTURES_DIR = Path(__file__).with_name("fixtures")
VALID_INVOICE = FIXTURES_DIR / "valid_invoice.pdf"
EXPIRED_INVOICE = FIXTURES_DIR / "expired_invoice.pdf"


def file_digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalize_ocr_text(text):
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def assert_ocr_contains(text, expected):
    assert normalize_ocr_text(expected) in normalize_ocr_text(text)


def assert_field_contains(fields, field_name, expected):
    assert field_name in fields
    assert_ocr_contains(fields[field_name], expected)


def test_pdf_is_converted_into_page_images():
    images = privacy.rasterize_pdf(VALID_INVOICE.read_bytes())

    assert len(images) == 1
    assert images[0].ndim == 3
    assert images[0].shape[2] == 3
    assert images[0].shape[0] > 1000
    assert images[0].shape[1] > 1000


def test_extract_words_with_boxes_reads_invoice_text():
    image = privacy.rasterize_pdf(VALID_INVOICE.read_bytes())[0]
    words = privacy.extract_words_with_boxes(image)
    text = " ".join(word.text for word in words)

    assert_ocr_contains(text, "VisionCare")
    assert_ocr_contains(text, "VC-R1000")
    assert_ocr_contains(text, "VC1000-VALID-001")


def test_detect_pii_finds_synthetic_private_fields():
    image = privacy.rasterize_pdf(VALID_INVOICE.read_bytes())[0]
    words = privacy.extract_words_with_boxes(image)
    detections = privacy.detect_pii(words)
    reasons = {detection["reason"] for detection in detections}
    values = " ".join(detection["value_text"] for detection in detections)

    assert reasons == {"customer_name", "email", "phone", "home_address"}
    assert "Jane Demo" in values
    assert "jane.demo@example.com" in values
    assert "555-123-4567" in values
    assert "123 Privacy Lane" in values


def test_redact_boxes_changes_detected_regions_only():
    image = privacy.rasterize_pdf(VALID_INVOICE.read_bytes())[0]
    words = privacy.extract_words_with_boxes(image)
    detections = privacy.detect_pii(words)
    redacted = privacy.redact_boxes(image, detections)

    assert redacted.shape == image.shape
    assert not np.array_equal(redacted, image)

    first_box = detections[0]
    redacted_region = redacted[
        first_box["top"] : first_box["bottom"],
        first_box["left"] : first_box["right"],
    ]
    assert redacted_region.mean() < 20


def test_sanitize_document_removes_pii_and_keeps_product_fields():
    result = privacy.sanitize_document(VALID_INVOICE.read_bytes())
    sanitized_text = result["sanitized_text"]

    assert result["verification"]["passed"] is True
    assert result["verification"]["remaining_values"] == []
    normalized_text = normalize_ocr_text(sanitized_text)
    assert normalize_ocr_text("Jane Demo") not in normalized_text
    assert normalize_ocr_text("jane.demo@example.com") not in normalized_text
    assert normalize_ocr_text("555-123-4567") not in normalized_text
    assert normalize_ocr_text("123 Privacy Lane") not in normalized_text
    assert_ocr_contains(sanitized_text, "VisionCare Demo Router")
    assert_ocr_contains(sanitized_text, "VC-R1000")
    assert_ocr_contains(sanitized_text, "2026-01-15")
    assert_ocr_contains(sanitized_text, "VC1000-VALID-001")

    fields = result["extracted_fields"]
    assert_field_contains(fields, "product_name", "VisionCare Demo Router")
    assert_field_contains(fields, "model_number", "VC-R1000")
    assert_field_contains(fields, "purchase_date", "2026-01-15")
    assert_field_contains(fields, "serial_number", "VC1000-VALID-001")


def test_expired_invoice_product_fields_remain_available():
    result = privacy.sanitize_document(EXPIRED_INVOICE.read_bytes())

    assert result["verification"]["passed"] is True
    fields = result["extracted_fields"]
    assert_field_contains(fields, "product_name", "VisionCare Demo Router")
    assert_field_contains(fields, "model_number", "VC-R1000")
    assert_field_contains(fields, "purchase_date", "2023-01-15")
    assert_field_contains(fields, "serial_number", "VC1000-EXPIRED-001")


def test_original_invoice_fixture_is_not_modified():
    before = file_digest(VALID_INVOICE)

    privacy.sanitize_document(VALID_INVOICE.read_bytes())

    assert file_digest(VALID_INVOICE) == before
