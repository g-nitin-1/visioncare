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
│ model_gateway.py     │        │ (OpenRouter)       │
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

Model provider code belongs in `backend/model_gateway.py`.

---

## Build status

Implemented in phases (see [`implementation.md`](./implementation.md)). Each phase has its own pytest suite and must pass before the next begins.

- [x] **Phase 0** — Bootstrap (skeleton, deps, smoke test)
- [x] **Phase 1** — Mock backend tools (warranty / RMA / escalate, idempotent)
- [x] **Phase 2** — Telemetry + DuckDB analytics
- [x] **Phase 3** — Manual loader
- [x] **Phase 4** — Local privacy pipeline
- [x] **Phase 5** — Model gateway (real + fake adapter)
- [x] **Phase 6** — Agent orchestration
- [x] **Phase 7** — Streamlit user view
- [x] **Phase 8** — Streamlit admin dashboard
- [x] **Phase 9** — End-to-end tests
- [ ] **Phase 10** — Documentation + demo rehearsal

---

## Setup

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add MODEL_API_KEY only for live OpenRouter use
python -m pytest -q    # all tests should pass inside the venv
```

Python 3.11+ is required. The examples use `python3.12`; use your local Python 3.11+ executable if it has a different name. Tesseract OCR is needed from Phase 4 onward; install with `sudo apt install tesseract-ocr` on Debian/Ubuntu.

---

## Model Provider

Phase 5 uses OpenRouter through `backend/model_gateway.py`.

Deterministic demo-day default:

```text
MODEL_PROVIDER=fake
MODEL_BASE_URL=https://openrouter.ai/api/v1
PRIMARY_MODEL=openrouter/free
```

Fake mode uses no API key, credits, or network calls. Use it for the scripted
presentation so model output and latency remain predictable.

For a live bonus demonstration, set `MODEL_PROVIDER=openrouter`. The configured
`openrouter/free` router dynamically chooses a free model, so output quality,
tool behavior, latency, and availability can vary. It does not guarantee
identical behavior between requests. Paid billing is not required, but an
OpenRouter API key is required.

Before demo day, review the account's OpenRouter privacy settings and run the
live smoke test. Restrictive data-policy settings can leave the free router
without an eligible endpoint. Free requests are also rate-limited; see
[provider data policies](https://openrouter.ai/docs/guides/privacy/provider-logging)
and [current API limits](https://openrouter.ai/docs/api/reference/limits).

For a pinned free multimodal model, use
`PRIMARY_MODEL=google/gemma-4-31b-it:free`. It is more predictable than the
dynamic router, but its upstream provider can still be unavailable or
rate-limited.

Offline tests use `FakeModelGateway` and do not call the provider. Live smoke tests require `MODEL_API_KEY` and are opt-in:

```bash
MODEL_PROVIDER=openrouter pytest -q -m live
```

OpenRouter sends image inputs through `/api/v1/chat/completions` using multipart message content and base64 `image_url` data URLs.

---

## Running the demo

```bash
streamlit run app.py
```

The customer view includes persistent chat, validated router/invoice uploads,
local raw-versus-redacted invoice proof, manual-backed steps, warranty results,
RMA tracking, and escalation tickets. Choose the troubleshooting outcome before
submitting so replacement automation only runs after a confirmed unresolved
issue.

The **Admin dashboard** tab reads completed session summaries through DuckDB and
shows total sessions, active users, resolution rate, average handling time,
warranty claims, open RMAs, human escalations, sentiment, and issue categories.
Use **Refresh metrics** after a workflow or select a recent time window.

---

## Evaluation suite

The committed evaluation fixtures are:

| Fixture | Scenario |
| --- | --- |
| `tests/fixtures/router_red_light.jpg` | VC-R1000 with a red status light |
| `tests/fixtures/valid_invoice.pdf` | Synthetic active warranty |
| `tests/fixtures/expired_invoice.pdf` | Synthetic expired warranty |

Run the deterministic end-to-end matrix and full offline suite:

```bash
pytest -q tests/test_end_to_end.py
pytest -q
```

Run one opt-in live check against the committed router image:

```bash
MODEL_PROVIDER=openrouter pytest -q -m live
# or
MODEL_PROVIDER=openrouter python3 scripts/live_smoke.py
```

No invoice is sent by the live smoke check. The RMA scenario is conceptually an
RMA request, but its telemetry intent remains `warranty_check` because warranty
eligibility governs the automated replacement path and dashboard count.

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
- The verification step confirms detected values stayed redacted; it does not prove undiscovered PII is absent.
- The current detector is tuned to the synthetic invoice fixture format, including one-line address fields.
- Document redaction does not inspect ordinary product photos; visible notes, labels, or other incidental PII in an image are out of scope for v1.
- For production use, a stronger NER model and human review would be required.

No real customer data, API key, or `.env` file is committed.

---

## License

Hackathon submission — code is for demonstration purposes.
