import json

import pytest

from backend import manual_loader


def test_supported_model_number_resolves_to_product_id():
    result = manual_loader.identify_product(model_number="VC-R1000")

    assert result == {
        "status": "identified",
        "product_id": "router-demo-1000",
        "product_name": "VisionCare Demo Router",
        "model_number": "VC-R1000",
        "manual_file": "router_troubleshooting.md",
    }


def test_model_number_matching_is_normalized():
    result = manual_loader.identify_product(model_number=" vc r-1000 ")

    assert result["status"] == "identified"
    assert result["product_id"] == "router-demo-1000"


def test_unknown_model_number_requires_clarification():
    result = manual_loader.identify_product(model_number="VC-R9999")

    assert result["status"] == "clarification_required"
    assert result["product_id"] is None
    assert "model number" in result["message"]


def test_product_name_alone_does_not_guess_product():
    result = manual_loader.identify_product(product_name="VisionCare Demo Router")

    assert result["status"] == "clarification_required"
    assert result["product_id"] is None


def test_red_light_query_returns_focused_sections():
    result = manual_loader.load_manual_sections(
        "router-demo-1000", ["red", "light"]
    )
    titles = [section["title"] for section in result["sections"]]

    assert result["status"] == "loaded"
    assert titles[0] == "Red Status Light"
    assert "Power Cycle Steps" in titles
    assert "Factory Reset Warning" not in titles
    assert len(result["sections"]) <= 3


def test_power_cycle_query_returns_power_section():
    result = manual_loader.load_manual_sections(
        "router-demo-1000", "router needs reboot after boot loop"
    )
    titles = [section["title"] for section in result["sections"]]

    assert "Power Cycle Steps" in titles


def test_unrelated_query_does_not_return_entire_manual():
    result = manual_loader.load_manual_sections(
        "router-demo-1000", "billing refund delivery address"
    )

    assert result["status"] == "loaded"
    assert result["sections"] == []


def test_unknown_product_requires_clarification():
    result = manual_loader.load_manual_sections("unknown-product", "red light")

    assert result["status"] == "clarification_required"
    assert result["sections"] == []
    assert "model number" in result["message"]


def test_invalid_manifest_is_rejected(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "router-demo-1000": {
                    "product_name": "VisionCare Demo Router",
                    "model_number": "VC-R1000",
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing manual_file"):
        manual_loader.identify_product(
            model_number="VC-R1000", manifest_path=manifest_path
        )
