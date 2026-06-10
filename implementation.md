# VisionCare Implementation Guide

This guide turns the VisionCare blueprint into a working hackathon demo. Implement the phases in order. Do not move to the next phase until the current phase passes its completion tests.

## Target Demo

The finished demo must support one reliable end-to-end path:

1. A user uploads a router image and a synthetic invoice.
2. Personal information is redacted locally before any cloud request.
3. The agent identifies the supported router model.
4. The matching troubleshooting manual sections are loaded.
5. The agent checks the warranty using a mock backend.
6. The agent suggests troubleshooting steps.
7. The agent opens an RMA only when the product is eligible and the issue requires it.
8. The interaction is written to `audit_logs.jsonl`.
9. The Streamlit admin dashboard displays the updated metrics.

## Implementation Rules

* Use Python 3.11 or later.
* Keep all external model calls behind `backend/model_gateway.py`.
* Keep the initial demo deterministic by supporting one router model first.
* Use synthetic fixtures only. Do not use real customer invoices or personal data.
* Store secrets only in `.env`. Commit `.env.example`, never `.env`.
* Write automated tests for each backend phase before adding UI behavior.
* Run the full test suite after every phase:

```bash
pytest -q
```

---

# Phase 0: Project Bootstrap

## Goal

Create a runnable Python project with the required folders, dependency file, secret template, and test setup.

## Implement

1. Create this initial structure:

```text
visioncare/
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
├── backend/
│   └── __init__.py
├── data/
│   ├── manuals/
│   └── invoices/
├── frontend/
│   └── __init__.py
└── tests/
    ├── __init__.py
    ├── test_smoke.py
    └── fixtures/
```

2. Add these initial dependencies to `requirements.txt`:

```text
streamlit
duckdb
filelock
python-dotenv
pytest
```

3. Add optional dependencies when their phases begin:

```text
opencv-python-headless
pymupdf
pytesseract
requests
```

4. Add `.env.example`:

```text
MODEL_PROVIDER=openrouter
MODEL_API_KEY=
MODEL_BASE_URL=
PRIMARY_MODEL=
```

5. Add `.gitignore` entries for `.env`, Python caches, test caches, local virtual environments, and generated runtime files.

6. Do not commit `data/audit_logs.jsonl`. It is ignored by git and is created at runtime by the telemetry logger or demo reset script.

7. Add one smoke test to `tests/test_smoke.py`:

```python
def test_project_bootstrapped():
    assert True
```

## Test Completion

Run:

```bash
python3.12 -m compileall .
pytest -q
git status --short
```

Verify:

* Python files compile.
* `pytest` passes the initial smoke test.
* `.env` would be ignored by git.
* `.env.example` remains visible to git.

## Expected Outcome

The repository has a clean Python skeleton that every teammate can install and run locally.

---

# Phase 1: Mock Backend Tools

## Goal

Implement deterministic warranty checks, RMA creation, and human escalation without depending on a cloud model.

## Implement

1. Create `backend/mock_db.json` with:

```json
{
  "products": {},
  "warranties": {},
  "rmas": {},
  "escalations": {}
}
```

2. Seed at least:

* One supported router product.
* One serial number with a valid warranty.
* One serial number with an expired warranty.

3. Create `backend/tools.py` with:

```python
check_warranty_status(serial_number)
initiate_rma_process(user_id, product_id, defect_description, idempotency_key)
escalate_to_human(priority_level, chat_history_summary, idempotency_key)
```

4. Implement these rules:

* Unknown serial numbers return a clear `not_found` result.
* The RMA tool does not check warranty state. Warranty eligibility is enforced by the Phase 6 agent before it calls `initiate_rma_process`.
* RMA and escalation writes use `filelock.FileLock`.
* Persist updates by writing a temporary file and atomically replacing `mock_db.json`.
* Repeated requests with the same `idempotency_key` return the existing RMA or escalation ticket.
* Real demo runs against the default `backend/mock_db.json` will mutate that tracked seed file. Do not commit demo-mutated RMA or escalation records; Phase 10 adds a reset script to restore deterministic seed state.

5. Create `tests/test_tools.py`. Run tool tests against a temporary copy of `mock_db.json` so the committed demo seed is not mutated.

## Test Completion

Run:

```bash
pytest -q tests/test_tools.py
```

Test at least:

* A valid serial number returns `valid: true`.
* An expired serial number returns `valid: false`.
* An unknown serial number returns `not_found`.
* An RMA request creates one RMA ID.
* Repeating the same RMA request returns the same ID and does not add a second record.
* An escalation request creates one ticket.
* Repeating the same escalation request returns the same ticket.

## Expected Outcome

The three agent tools work offline, return predictable JSON-compatible dictionaries, and do not create duplicate tickets.

---

# Phase 2: Telemetry And DuckDB Analytics

## Goal

Persist immutable events and calculate admin dashboard metrics from completed sessions.

## Implement

1. Create `backend/logger.py`.

2. Add an `append_event(event)` function that:

* Adds an `event_id` if missing.
* Adds a UTC timestamp if missing.
* Requires `schema_version`, `session_id`, and `event_type`.
* Appends exactly one JSON object per line to `data/audit_logs.jsonl`.
* Uses a file lock while appending.

3. Support these event types:

```text
tool_call
session_summary
```

4. For `session_summary`, require:

```text
intent
anonymous_user_id
issue_category
hardware_issue_detected
sentiment
resolved
escalated
rma_id
model
latency_ms
resolution_time_sec
```

5. Generate the required opaque `anonymous_user_id` locally for dashboard counts. Do not store a name, email address, or other personal identifier in telemetry.

6. Create `backend/analytics.py`.

7. Use DuckDB to read `data/audit_logs.jsonl`.

8. Calculate:

* Total completed sessions.
* Active users as distinct `anonymous_user_id` values within the selected dashboard time window.
* Resolution rate.
* Average handling time across all completed sessions, including unresolved or escalated sessions.
* Issue category counts.
* Sentiment counts.
* Human escalation count.
* Warranty claim count.
* Open RMA count as distinct non-null RMA IDs.

9. Ensure dashboard metrics query only `event_type = 'session_summary'`.

10. Create `tests/test_logger.py` and `tests/test_analytics.py`. Use temporary files in tests so committed fixtures are not mutated.

## Test Completion

Run:

```bash
pytest -q tests/test_logger.py tests/test_analytics.py
```

Test at least:

* Appending two events creates two valid JSONL rows.
* Missing required fields are rejected.
* Tool-call rows do not increase total session count.
* Two session summaries produce the correct resolution rate and average handling time.
* Repeated summaries for one opaque user count as one active user.
* Escalated sessions increase the escalation count.
* RMA sessions increase the open RMA count.
* An empty audit log returns zero-valued metrics instead of crashing.

## Expected Outcome

The application can log support activity and derive stable metrics without using DuckDB as a transactional database.

---

# Phase 3: Product Manual Loader

## Goal

Select relevant troubleshooting text after the product has been identified.

## Implement

1. Create `data/manuals/manifest.json`.

2. Add one supported router entry:

```json
{
  "router-demo-1000": {
    "product_name": "VisionCare Demo Router",
    "model_number": "VC-R1000",
    "manual_file": "router_troubleshooting.md"
  }
}
```

3. Create `data/manuals/router_troubleshooting.md` with short sections for:

* Red status light.
* Power cycle steps.
* Cable checks.
* Factory reset warning.
* Escalation conditions.

4. Create `backend/manual_loader.py`.

5. Implement:

```python
identify_product(product_name=None, model_number=None)
load_manual_sections(product_id, issue_keywords)
```

6. Keep lookup deterministic:

* Match the supported model number exactly after normalization.
* Return a clarification-required result when the product is unknown.
* Return only relevant sections, not the entire manual.

7. Create `tests/test_manual_loader.py`.

## Test Completion

Run:

```bash
pytest -q tests/test_manual_loader.py
```

Test at least:

* `VC-R1000` resolves to `router-demo-1000`.
* Unknown model numbers return a clarification-required result.
* A red-light query returns the red-light troubleshooting section.
* An unrelated query does not return every manual section.

## Expected Outcome

The agent can load focused manual context for the supported router without a vector database.

---

# Phase 4: Local Privacy Pipeline

## Goal

Redact personal information from synthetic invoices before any cloud API call.

## Implement

1. Add:

```text
opencv-python-headless
pymupdf
pytesseract
```

2. Confirm the local machine has the Tesseract OCR binary installed. Document the setup command in `README.md`.

3. Create `backend/privacy.py`.

4. Implement:

```python
rasterize_pdf(pdf_bytes)
extract_words_with_boxes(image)
detect_pii(words)
redact_boxes(image, boxes)
verify_redaction(redacted_image)
sanitize_document(pdf_bytes)
```

5. Implement regex detection for:

* Email addresses.
* Phone numbers.
* Common invoice labels followed by customer names.
* Address blocks in the synthetic fixture format.

6. Keep the NER detector behind an interface so a local NER model can be added later without rewriting the pipeline.

7. Retain only:

* Product name.
* Model number.
* Purchase date.
* Serial number.

8. Create two synthetic invoice fixtures:

```text
tests/fixtures/valid_invoice.pdf
tests/fixtures/expired_invoice.pdf
```

9. Create `tests/test_privacy.py`.

## Test Completion

Run:

```bash
pytest -q tests/test_privacy.py
```

Test at least:

* The PDF is converted into page images.
* Synthetic email, phone number, name, and address fields are detected.
* The sanitized OCR output no longer contains those values.
* Product model, serial number, and purchase date remain available using normalized comparisons so tests tolerate OCR tokenizer differences.
* The original invoice fixture is not modified.

Perform one manual visual check by opening the sanitized invoice image and confirming that the expected regions are obscured.

## Expected Outcome

Synthetic invoices can be sanitized locally while preserving the product fields required for the warranty workflow.

Limitations:

* `verify_redaction` verifies that detected PII values stayed redacted; it is not an oracle for missed PII.
* Address detection is scoped to the synthetic fixture format and one-line address fields.

---

# Phase 5: Model Gateway

## Goal

Connect one pinned multimodal cloud model through a replaceable adapter.

## Implement

1. Select one provider and one exact model ID. Record them in `.env.example` and `README.md`.

2. Create `backend/model_gateway.py`.

3. Implement a `ModelGateway` interface with:

```python
analyze_product_image(image_bytes, prompt)
extract_document_fields(sanitized_images, prompt)
respond_with_tools(messages, tools)
```

4. Implement one real provider adapter.

5. Implement `FakeModelGateway` for tests. It must return deterministic fixture responses without network access.

6. Add timeouts and clear errors for:

* Missing API key.
* Unsupported file type.
* Provider timeout.
* Invalid provider response.

7. Keep provider-specific request and response formats inside this module only.

8. Create `tests/test_model_gateway.py`.

9. After the real provider adapter and optional Phase 4 dependencies are installed, pin dependency versions for teammate reproducibility:

```bash
python -m pip freeze > requirements.lock.txt
```

Keep `requirements.txt` readable for the hackathon setup path, and use the lock file when exact environment reproduction matters.

## Test Completion

Run offline tests:

```bash
pytest -q tests/test_model_gateway.py
```

Verify:

* Fake gateway returns the expected router model and issue.
* Missing API key produces a useful error.
* Invalid provider responses are handled without crashing the application.

Run one opt-in live smoke test with a configured API key:

```bash
pytest -q -m live
```

Verify:

* The provider accepts one synthetic router image.
* The response identifies the expected product family or returns a controlled clarification request.
* No unredacted invoice is sent during the test.

## Expected Outcome

The project has one working cloud-model integration and a deterministic fake adapter for routine development.

---

# Phase 6: Agent Orchestration

## Goal

Connect privacy processing, model analysis, manual loading, tools, and telemetry into one support workflow.

## Implement

1. Create `backend/agent.py`.

2. Add a session state object with:

```text
session_id
user_id
anonymous_user_id
messages
product_id
serial_number
intent
diagnosis_attempts
sentiment
resolved
escalated
rma_id
```

3. Implement this sequence:

```text
receive upload
-> sanitize document locally when present
-> analyze sanitized content
-> extract model_number explicitly from image, text, or permitted OCR fields
-> identify supported product
-> load matching manual sections
-> determine intent
-> call warranty, RMA, or escalation tool when required
-> generate user-facing response
-> append tool-call events
-> append final session-summary event
```

4. Apply escalation rules:

* Highly negative sentiment.
* Diagnosis still unknown after two attempts.
* Confidence below the configured threshold.

Product identification contract:

* Call `identify_product` with an extracted `model_number`; product name alone does not select a manual.
* If the model returns only a product name or cannot read `VC-R1000`, ask the user for the model number instead of guessing.
* Branch only on the exact loader statuses: `identified`, `loaded`, and `clarification_required`.

5. Apply RMA policy:

* Check warranty first.
* Never open an RMA automatically for an expired warranty.
* Enforce the warranty gate in the agent. `initiate_rma_process` only knows whether the product exists; it intentionally does not inspect warranty state.
* Emit `intent: "warranty_check"` for warranty-related session summaries so analytics can count warranty flows predictably.
* Use a stable idempotency key so retries do not duplicate RMAs.

6. Create `tests/test_agent.py` using `FakeModelGateway`.

## Test Completion

Run:

```bash
pytest -q tests/test_agent.py
```

Test at least:

* Valid router warranty plus unresolved defect creates one RMA.
* Repeating the request returns the same RMA ID.
* Expired warranty does not create an RMA.
* Unknown product asks for clarification.
* Two failed diagnosis attempts create one escalation ticket.
* A completed workflow appends one session-summary event.

## Expected Outcome

A deterministic backend workflow completes the full support journey without a UI.

---

# Phase 7: Streamlit User View

## Goal

Expose the support workflow through a simple customer-facing interface.

## Implement

1. Create `frontend/user_view.py`.

2. Add:

* Chat history.
* Image uploader.
* PDF invoice uploader.
* Submit button.
* Processing status.
* Clear display of diagnosis, warranty result, troubleshooting steps, RMA ID, or escalation ticket.

3. Use `st.session_state` to preserve one agent session during reruns.

4. Reject unsupported file types and oversized uploads before processing.

5. Keep raw invoice images out of logs and dashboard state.

6. Wire the view into `app.py`.

7. Catch `ModelGatewayError` at the UI boundary and show a concise retry or
offline-mode message. Provider timeouts, rate limits, and unavailable free
endpoints must not expose a raw traceback.

## Test Completion

Run:

```bash
streamlit run app.py
```

Manually verify:

* The page loads without exceptions.
* A router image can be uploaded.
* A synthetic invoice can be uploaded.
* The response shows warranty and troubleshooting results.
* Re-clicking submit does not create a duplicate RMA.
* An unsupported file displays a clear error.
* A simulated provider timeout or rate-limit error displays a user-facing
  message and leaves the page usable.

## Expected Outcome

A judge can complete the router support flow from a browser without using the
terminal, and provider failures degrade to a clear error instead of crashing
the interface.

---

# Phase 8: Streamlit Admin Dashboard

## Goal

Show operational metrics from the JSONL event stream.

## Implement

1. Create `frontend/admin_dashboard.py`.

2. Display:

* Total sessions.
* Active users.
* Resolution rate.
* Average handling time.
* Warranty claims.
* Open RMAs.
* Human escalations.
* Sentiment distribution.
* Issue category distribution.

3. Add a refresh action or controlled auto-refresh interval.

4. Handle an empty audit log without errors.

5. Add the admin view to `app.py` as a second tab or pane.

## Test Completion

Run:

```bash
streamlit run app.py
```

Manually verify:

* An empty audit log renders zero-valued metrics.
* Completing a user workflow updates the metrics after refresh.
* Tool-call events do not inflate total sessions.
* An escalation workflow increments the escalation metric.

Also run:

```bash
pytest -q tests/test_analytics.py
```

## Expected Outcome

The admin dashboard visibly updates after demo interactions and reports session-level metrics correctly.

---

# Phase 9: Evaluation Fixtures And End-To-End Tests

## Goal

Make the demo repeatable and detect regressions before presentation.

## Implement

1. Add these synthetic fixtures:

```text
tests/fixtures/router_red_light.jpg
tests/fixtures/valid_invoice.pdf
tests/fixtures/expired_invoice.pdf
```

Only fixtures referenced by the test matrix below are listed. Do not commit unused image fixtures; they enlarge the repo and weaken the documentation score by implying coverage that does not exist.

2. Create `tests/test_end_to_end.py`.

3. Use `FakeModelGateway` for automated end-to-end tests.

4. Cover this matrix:

| Fixture | Expected Intent | Expected Tool | Expected Result |
| --- | --- | --- | --- |
| Router red light | Troubleshooting | None initially | Manual steps returned |
| Valid invoice | Warranty Check | `check_warranty_status` | Valid warranty shown |
| Eligible unresolved defect | RMA Request | `initiate_rma_process` | One RMA created |
| Expired invoice | Warranty Check | `check_warranty_status` | Out-of-warranty response |
| Unknown error after two attempts | Escalation | `escalate_to_human` | One ticket created |

5. Add one live smoke-test script for the selected cloud provider. Keep it opt-in so normal tests remain fast and do not consume API credits.

## Test Completion

Run:

```bash
pytest -q
pytest -q -m live
```

Verify:

* All offline tests pass.
* The opt-in live router smoke test passes with a configured API key.
* Re-running the suite does not mutate committed fixtures.
* Re-running the same end-to-end request does not create duplicate RMAs or escalations.

## Expected Outcome

The project has a stable automated regression suite and a documented live-provider check.

---

# Phase 10: Documentation And Demo Readiness

## Goal

Prepare the repository for teammates, judges, and a short live presentation.

## Implement

1. Expand `README.md` with:

* Problem statement.
* Architecture summary.
* Setup instructions.
* Environment variables.
* Tesseract installation instructions.
* How to run tests.
* How to start Streamlit.
* Demo walkthrough.
* Privacy limitations.

2. Add an architecture diagram showing:

```text
Upload
-> Local privacy pipeline
-> Model gateway
-> Agent orchestration
-> Manual loader
-> Mock tools
-> JSONL telemetry
-> DuckDB dashboard
```

3. Document limitations honestly:

* The demo supports one router model first.
* The backend is a local JSON mock, not a production database.
* Privacy detection is designed for synthetic fixtures and requires stronger validation before production use.
* Live model quality and latency depend on the selected provider.
* Open RMA count means distinct RMA IDs referenced by completed session summaries; closing RMAs is out of scope for v1.

4. Create a reset script that restores deterministic demo state:

```text
scripts/reset_demo.py
```

The reset script must restore `backend/mock_db.json` to its committed seed state, including empty `rmas` and `escalations`, because live demo runs mutate the default mock DB.

5. Rehearse the demo in this order:

```text
reset state
-> launch Streamlit
-> upload router image and valid invoice
-> show troubleshooting
-> trigger one eligible RMA
-> open admin dashboard
-> show updated metrics
```

## Test Completion

Run:

```bash
python scripts/reset_demo.py
pytest -q
streamlit run app.py
```

Perform a final manual rehearsal:

* A teammate can set up the project by following only `README.md`.
* The full demo completes in under five minutes.
* The reset script restores a known starting state.
* No `.env`, API key, raw customer data, or generated cache is tracked by git.
* The dashboard metrics match the visible demo actions.

## Expected Outcome

The repository is ready for a repeatable hackathon presentation and can be run by someone other than the original implementer.

---

# Final Definition Of Done

VisionCare is demo-ready when:

* `pytest -q` passes offline.
* The opt-in live provider smoke test passes.
* Uploaded synthetic invoices are sanitized before cloud transmission.
* A supported router issue loads relevant manual instructions.
* Warranty checks distinguish valid, expired, and unknown serial numbers.
* RMA and escalation requests are idempotent.
* One completed session produces one session-summary event.
* The Streamlit admin dashboard updates from DuckDB metrics.
* The reset script restores deterministic demo data.
* `README.md` lets a teammate run the project without undocumented steps.
