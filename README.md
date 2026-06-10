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
- [x] **Phase 10** — Documentation + demo rehearsal

---

## Setup

### Prerequisites

VisionCare requires:

* Python 3.11 or newer.
* Tesseract OCR.
* A local virtual environment.
* An OpenRouter API key only for the optional live-model check.

On Debian or Ubuntu:

```bash
sudo apt update
sudo apt install python3-venv tesseract-ocr
tesseract --version
```

Create the project environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.lock.txt
cp .env.example .env   # add MODEL_API_KEY only for live OpenRouter use
python scripts/reset_demo.py
python -m pytest -q
```

The repository defaults to the deterministic fake model, so setup and the
scripted demo require no billing, API key, or network call after dependencies
are installed. `requirements.lock.txt` contains the exact Python 3.12
environment validated for the demo. `requirements.txt` remains the short,
readable dependency list for intentionally resolving newer compatible versions.

The local OCR validation was performed with Tesseract 4.1.1. Newer Tesseract
versions are supported by normalized OCR assertions, but OCR text can still
vary slightly across platforms.

### Environment Variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `MODEL_PROVIDER` | `fake` | Use `fake` for deterministic demos or `openrouter` for a live bonus check. |
| `MODEL_API_KEY` | blank | OpenRouter key. Required only when `MODEL_PROVIDER=openrouter`. |
| `MODEL_BASE_URL` | `https://openrouter.ai/api/v1` | OpenAI-compatible provider endpoint. |
| `PRIMARY_MODEL` | `openrouter/free` | Live OpenRouter model or router identifier. |

Keep `.env` local. It is ignored by git and must never be committed.

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

## Testing

Run the offline regression suite:

```bash
python -m pytest -q
```

Run focused end-to-end scenarios:

```bash
python -m pytest -q tests/test_end_to_end.py
```

Run the optional live router-image check:

```bash
MODEL_PROVIDER=openrouter python -m pytest -q -m live
# or
MODEL_PROVIDER=openrouter python scripts/live_smoke.py
```

Normal pytest runs exclude the `live` marker, even when a key exists, so they
cannot accidentally consume provider credits.

---

## Running the demo

Start from a known state and launch Streamlit:

```bash
python scripts/reset_demo.py
python -m streamlit run app.py
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

### Reset And Preflight

Stop Streamlit before resetting. The reset restores
`backend/mock_db.json` byte-for-byte from `backend/mock_db.seed.json` and removes
the ignored runtime telemetry log:

```bash
python scripts/reset_demo.py
```

Run the deterministic non-browser rehearsal:

```bash
python scripts/rehearse_demo.py
```

The rehearsal uses the real router image, real local invoice redaction,
`FakeModelGateway`, the mock tools, JSONL telemetry, and DuckDB. It must report:

```text
RMA: RMA-0001
Dashboard: 1 session, 1 open RMA, 1 warranty claim
```

Run `python scripts/reset_demo.py` again before the judged walkthrough.

### Five-Minute Walkthrough

1. Run `python scripts/reset_demo.py`.
2. Run `python -m streamlit run app.py`.
3. In **Customer support**, upload
   `tests/fixtures/router_red_light.jpg`.
4. Enter `My VC-R1000 router has a red status light.`, leave the outcome as
   **Not confirmed yet**, and submit.
5. Show the manual-backed red-light and power-cycle instructions.
6. Upload `tests/fixtures/valid_invoice.pdf`.
7. Enter `The red light remains after the power cycle and cable checks. Please
   replace the router.`, select **Still broken after troubleshooting**, and
   submit.
8. Show the raw-versus-redacted invoice comparison, valid warranty, and
   `RMA-0001`.
9. Submit the same request again and show that the RMA remains `RMA-0001`.
10. Open **Admin dashboard**, click **Refresh metrics**, and show one completed
    session, one warranty claim, and one open RMA.
11. After the presentation, stop Streamlit and run
    `python scripts/reset_demo.py`.

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
MODEL_PROVIDER=openrouter python scripts/live_smoke.py
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

## Limitations

* V1 supports one product: the VisionCare Demo Router (`VC-R1000`).
* `backend/mock_db.json` is a local demo backend, not a transactional production
  database.
* Live model quality, tool behavior, latency, and availability depend on the
  selected provider.
* `openrouter/free` dynamically selects a model and is rate-limited.
* Open RMA count means distinct RMA IDs referenced by completed sessions. V1
  has no close-RMA workflow.
* The manual matcher is a deterministic heuristic tuned to the single demo
  manual, not general-purpose retrieval.
* Highly negative sentiment intentionally routes to a human before automated
  warranty or RMA handling.

### Privacy Scope

The local redaction pipeline is designed for the committed synthetic invoices.
It is **not** a production-grade PII guarantee:

- Regex + OCR misses can leak data on real documents.
- The verification step confirms detected values stayed redacted; it does not prove undiscovered PII is absent.
- The current detector is tuned to the synthetic invoice fixture format, including one-line address fields.
- Document redaction does not inspect ordinary product photos; visible notes, labels, or other incidental PII in an image are out of scope for v1.
- For production use, a stronger NER model and human review would be required.

Only synthetic fixtures should be used in the demo. No real customer data, API
key, `.env` file, runtime telemetry, or generated cache belongs in git.

---

## Demo Readiness

The final validated state is:

* `94` offline tests pass in the locked Python 3.12 environment.
* The opt-in live router-image smoke test passes.
* The deterministic rehearsal completes in under ten seconds.
* The reset command restores byte-identical seed data and zero dashboard
  metrics.
* Streamlit starts successfully and reports a healthy server.
* Fixture hashes remain unchanged across repeated test and rehearsal runs.

---

## License

Hackathon submission — code is for demonstration purposes.
