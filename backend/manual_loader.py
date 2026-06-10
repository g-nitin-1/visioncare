"""Deterministic product manual lookup for the VisionCare demo."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


MANUALS_DIR = Path(__file__).resolve().parents[1] / "data" / "manuals"
MANIFEST_PATH = MANUALS_DIR / "manifest.json"
SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
TOKEN_RE = re.compile(r"[a-z0-9]+")
KEYWORD_ALIASES = {
    "red": {"red", "warning", "status", "light", "led", "error"},
    "light": {"light", "led", "status", "warning"},
    "power": {"power", "cycle", "reboot", "restart", "boot", "adapter"},
    "cable": {"cable", "wan", "lan", "modem", "frayed", "bent"},
    "reset": {"reset", "factory", "configuration", "password"},
    "escalation": {"escalate", "escalation", "support", "human", "smoke", "heat"},
}
RESET_TERMS = {"reset", "factory"}
# Retrieval rules are intentionally tuned for the single demo router manual.
# Adding products or changing manual wording requires reviewing these aliases/gates.


def identify_product(
    product_name: str | None = None,
    model_number: str | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Identify a product by model number; product_name is context only."""
    manifest = _load_manifest(_resolve_manifest_path(manifest_path))
    normalized_model = _normalize_model_number(model_number)

    if normalized_model:
        for product_id, product in manifest.items():
            if _normalize_model_number(product.get("model_number")) == normalized_model:
                return {
                    "status": "identified",
                    "product_id": product_id,
                    "product_name": product["product_name"],
                    "model_number": product["model_number"],
                    "manual_file": product["manual_file"],
                }

    return {
        "status": "clarification_required",
        "product_id": None,
        "message": (
            "Please provide the product model number so I can load the "
            "correct troubleshooting manual."
        ),
    }


def load_manual_sections(
    product_id: str,
    issue_keywords: str | list[str] | tuple[str, ...],
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return relevant manual sections for a supported product and issue."""
    resolved_manifest_path = _resolve_manifest_path(manifest_path)
    manifest = _load_manifest(resolved_manifest_path)

    if product_id not in manifest:
        return {
            "status": "clarification_required",
            "product_id": product_id,
            "sections": [],
            "message": "Unknown product. Please confirm the product model number.",
        }

    manual_path = resolved_manifest_path.parent / manifest[product_id]["manual_file"]
    sections = _parse_manual_sections(manual_path.read_text(encoding="utf-8"))
    wanted_terms = _expand_issue_terms(issue_keywords)
    matched_sections = _match_sections(sections, wanted_terms)

    return {
        "status": "loaded",
        "product_id": product_id,
        "manual_file": manifest[product_id]["manual_file"],
        "sections": matched_sections,
    }


def _resolve_manifest_path(manifest_path: str | Path | None) -> Path:
    return Path(manifest_path) if manifest_path is not None else MANIFEST_PATH


def _load_manifest(manifest_path: Path) -> dict[str, dict[str, str]]:
    with manifest_path.open("r", encoding="utf-8") as handle:
        manifest = json.load(handle)

    if not isinstance(manifest, dict):
        raise ValueError("manual manifest must be a JSON object")

    for product_id, product in manifest.items():
        if not isinstance(product, dict):
            raise ValueError(f"manifest product must be an object: {product_id}")
        for field_name in ("product_name", "model_number", "manual_file"):
            if not str(product.get(field_name, "")).strip():
                raise ValueError(
                    f"manifest product {product_id} is missing {field_name}"
                )

    return manifest


def _normalize_model_number(value: str | None) -> str:
    if value is None:
        return ""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def _parse_manual_sections(manual_text: str) -> list[dict[str, str]]:
    matches = list(SECTION_HEADING_RE.finditer(manual_text))
    sections: list[dict[str, str]] = []

    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(manual_text)
        title = match.group(1).strip()
        content = manual_text[start:end].strip()
        sections.append(
            {
                "title": title,
                "content": content,
                "text": f"{title}\n{content}",
            }
        )

    return sections


def _expand_issue_terms(issue_keywords: str | list[str] | tuple[str, ...]) -> set[str]:
    if isinstance(issue_keywords, str):
        raw_terms = _tokens(issue_keywords)
    else:
        raw_terms = []
        for keyword in issue_keywords:
            raw_terms.extend(_tokens(str(keyword)))

    expanded: set[str] = set()
    for term in raw_terms:
        expanded.add(term)
        expanded.update(KEYWORD_ALIASES.get(term, set()))
    return expanded


def _match_sections(
    sections: list[dict[str, str]], wanted_terms: set[str]
) -> list[dict[str, str]]:
    if not wanted_terms:
        return []

    scored_sections: list[tuple[int, int, int, dict[str, str]]] = []
    for position, section in enumerate(sections):
        section_title_terms = set(_tokens(section["title"]))
        if section_title_terms.intersection(RESET_TERMS) and not wanted_terms.intersection(
            RESET_TERMS
        ):
            continue

        section_terms = set(_tokens(section["text"]))
        title_score = len(wanted_terms.intersection(section_title_terms))
        score = len(wanted_terms.intersection(section_terms)) + (title_score * 3)
        if score > 0:
            scored_sections.append((score, title_score, position, section))

    scored_sections.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [
        {"title": section["title"], "content": section["content"]}
        for _, _, _, section in scored_sections[:3]
    ]


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())
