# VisionCare

> Multimodal customer support agent for hardware companies.
> Users *show* the problem with a photo or invoice — the agent diagnoses, checks warranty, and triggers RMAs.

**FlowZint AI Hackathon 2026 submission**
**Track:** Customer Care Bot — *"Resolve queries, retain customers, build trust."*

---

## Problem

Hardware support is expensive. A frustrated customer types "my router is broken" and a human agent spends 8 minutes asking what color the lights are, what model it is, and where the receipt is. Vision-capable models can do that triage in seconds — but most support bots ship without multimodal input.

VisionCare lets a user upload a photo of the broken product and a synthetic invoice, then runs the full support flow autonomously: identify the model, check warranty, walk through troubleshooting, and open an RMA only when the product is eligible. Personal information is redacted on-device before any cloud call.

---

## Architecture

```
┌──────────────┐
│ User upload  │ image + invoice + text
└──────┬───────┘
       ▼
┌──────────────────────┐
│ Local privacy layer  │ rasterize → OCR → PII detect → redact → verify
└──────┬───────────────┘ (runs before any cloud transmission)
       ▼
┌──────────────────────┐        ┌────────────────────┐
│ Model gateway        │◀──────▶│ Cloud vision model │
│ (backend/gateway.py) │        │ (OpenRouter)       │
└──────┬───────────────┘        └────────────────────┘
       ▼
┌──────────────────────┐        ┌────────────────────┐
│ Agent orchestrator   │◀──────▶│ Manual loader      │
│ (backend/agent.py)   │        │ (manifest + md)    │
└──────┬───────────────┘        └────────────────────┘
       ▼
┌──────────────────────┐
│ Tools (mock_db.json) │ check_warranty │ initiate_rma │ escalate
└──────┬───────────────┘ file-locked, idempotent
       ▼
┌──────────────────────┐        ┌────────────────────┐
│ JSONL telemetry      │───────▶│ DuckDB analytics   │
│ (audit_logs.jsonl)   │        │ → Streamlit admin  │
└──────────────────────┘        └────────────────────┘
```

**Why no vector database:** the demo supports one product family. Manual sections are short, deterministic, and selected by manifest lookup — cheaper and more reliable than RAG for this scope.

---

## Build status

Implemented in phases (see [`implementation.md`](./implementation.md)). Each phase has its own pytest suite and must pass before the next begins.

- [x] **Phase 0** — Bootstrap (skeleton, deps, smoke test)
- [ ] **Phase 1** — Mock backend tools (warranty / RMA / escalate, idempotent)
- [ ] **Phase 2** — Telemetry + DuckDB analytics
- [ ] **Phase 3** — Manual loader
- [ ] **Phase 4** — Local privacy pipeline
- [ ] **Phase 5** — Model gateway (real + fake adapter)
- [ ] **Phase 6** — Agent orchestration
- [ ] **Phase 7** — Streamlit user view
- [ ] **Phase 8** — Streamlit admin dashboard
- [ ] **Phase 9** — End-to-end tests
- [ ] **Phase 10** — Documentation + demo rehearsal

---

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in MODEL_API_KEY
pytest -q              # all tests should pass
```

Python 3.11+ is required. Tesseract OCR is needed from Phase 4 onward; install with `sudo apt install tesseract-ocr` on Debian/Ubuntu.

---

## Running the demo

```bash
streamlit run app.py
```

Tabs: **User view** (chat + uploads) and **Admin dashboard** (DuckDB-powered metrics).

---

## Demo video plan (90 seconds)

| Time | Shot | Why |
|------|------|-----|
| 0:00–0:10 | Title card + one-line problem statement | Anchors the judge |
| 0:10–0:35 | User uploads router photo + invoice, agent identifies model and checks warranty | Shows multimodal in action |
| 0:35–1:05 | **Side-by-side: raw invoice vs. redacted invoice that goes to the cloud** | The unfakeable differentiator — most submissions won't have this |
| 1:05–1:25 | RMA gets opened; re-submit shows same RMA ID (idempotency) | Signals real engineering, not a wrapper |
| 1:25–1:30 | Admin dashboard updates live | Closes the loop on telemetry |

The privacy-redaction shot is non-negotiable — it's the visual that separates this from every other "support chatbot" submission.

---

## Privacy notes

The local redaction pipeline (Phase 4) is designed for synthetic invoices. It is **not** a production-grade PII guarantee:

- Regex + OCR misses can leak data on real documents.
- The verification step checks against detected tokens only, not against an oracle.
- For production use, a stronger NER model and human review would be required.

No real customer data, API key, or `.env` file is committed.

---

## License

Hackathon submission — code is for demonstration purposes.
