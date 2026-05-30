# VisionCare: Omni-Channel Multimodal Support Agent

**FlowZint AI Hackathon 2026 Submission Blueprint**

## The Core Concept

A customer support bot designed for hardware, consumer electronics, or retail companies that allows users to *show* their problem rather than just typing it out. The agent processes text, images of broken products or error screens, and document uploads (like invoices or warranty cards) to autonomously diagnose issues, trigger workflows, and log real-time analytics.

---

# 1. System Architecture & Design Decisions

## Model Serving Approach: Serverless Open-Source Cloud

To maximize team collaboration without hardware bottlenecks, we will use a serverless API provider (e.g., OpenRouter, Together AI).

* **Primary Model:** **Llama 3.2 Vision (11B or 90B)** for reasoning, complex tool-calling, and general image analysis.
* **Secondary Model (Fallback for OCR):** **Pixtral 12B** specifically for extracting text from messy warranty cards or scanned invoices.
* **Implementation:** The API key is stored in a `.env` file, allowing all teammates to run the Streamlit frontend locally while hitting the same cloud-hosted model endpoints.

## Knowledge Retrieval (RAG Layer): Product-Aware In-Context Learning

Instead of a complex vector database (traditional RAG), we will leverage the large context windows of modern models.

* **Routing Step:** The model first identifies the product family and model number from the user's text, image, or OCR output. If the product cannot be identified confidently, the agent asks the user for clarification.
* **Strategy:** The application selects the matching manual from a local manifest and injects only the relevant troubleshooting sections into the next model request.
* **Demo Scope:** The initial demo ships with one supported router model and one matching troubleshooting manual. This keeps the lookup deterministic while preserving a clear path to additional products.
* **Advantage:** The model can cross-reference the uploaded image against targeted manual text without the operational complexity of a vector database.

## Telemetry & Analytics Backend (DuckDB)

DuckDB will act as the analytical engine for the admin dashboard, processing persisted events rather than handling concurrent transactional queries.

### Event Schema

Every significant interaction appends an immutable event. Tool calls use `event_type: "tool_call"` and completed support sessions use `event_type: "session_summary"`. Dashboard metrics are calculated from session summary events so message-level events do not inflate totals.

```json
{
  "event_id": "evt_01JX8QK3G6R8V2",
  "schema_version": 1,
  "timestamp": "2026-05-30T11:15:00Z",
  "session_id": "user_123",
  "event_type": "session_summary",
  "intent": "warranty_claim",
  "issue_category": "router_connectivity",
  "hardware_issue_detected": true,
  "sentiment": "frustrated",
  "resolved": true,
  "escalated": false,
  "rma_id": "RMA-9982",
  "model": "provider/model-id",
  "latency_ms": 1200,
  "resolution_time_sec": 45
}
```

### Persistence

These JSON events are appended locally to an `audit_logs.jsonl` file.

### Analytics

DuckDB reads directly from `audit_logs.jsonl` to instantly populate the Streamlit Admin dashboard, displaying:

* Resolution rates
* Average handling time
* Issue categories
* Sentiment trends
* Human escalation frequency

---

# 2. Agentic Workflow & Tool Use

## Tool Schemas & Mock Backend

The agent will execute function calls against a simulated local backend (`mock_db.json`) for repeatable demo state. JSON storage alone does not guarantee idempotency: mutating operations require an `idempotency_key`, return an existing result for repeated requests, and update the file using a lock plus atomic replacement.

### 1. `check_warranty_status(serial_number)`

**Input**

* Extracted serial number from an image

**Output**

* Warranty status (Valid/Expired)
* Expiration date

Example:

```json
{
  "valid": true,
  "expiration_date": "2027-03-15"
}
```

---

### 2. `initiate_rma_process(user_id, product_id, defect_description, idempotency_key)`

**Input**

* User ID
* Identified product
* Model-generated defect summary
* Stable idempotency key derived from the session and request type

**Output**

* Generated RMA tracking number
* Existing RMA tracking number when the same idempotency key is submitted again

Example:

```json
{
  "rma_id": "RMA-9982"
}
```

---

### 3. `escalate_to_human(priority_level, chat_history_summary, idempotency_key)`

**Input**

* Priority level (Low/High)
* Conversation summary
* Stable idempotency key derived from the session and escalation reason

**Trigger Conditions**

* User sentiment becomes highly negative
* The model fails to diagnose the issue after two attempts
* Confidence score falls below a predefined threshold

**Output**

```json
{
  "ticket_id": "ESC-4312",
  "assigned_queue": "hardware_support"
}
```

---

## End-to-End Workflow

1. User uploads an image, document, or text query.
2. Local privacy preprocessing sanitizes uploaded documents and extracts permitted product fields with OCR.
3. Vision model analyzes the sanitized content.
4. Agent identifies the product from the user's text, image, or permitted OCR fields.
5. Agent loads the matching manual sections.
6. Agent determines intent:

   * Troubleshooting
   * Warranty Check
   * RMA Request
   * Human Escalation
7. Appropriate tool is called.
8. Tool result is returned to the agent.
9. Agent generates a user-facing response.
10. Event is logged into `audit_logs.jsonl`.
11. DuckDB dashboard updates in real time.

---

# 3. Evaluation & Testing Strategy

## Evaluation Fixtures

The repository will include a `tests/fixtures/` directory containing sample data to reliably demonstrate the bot's capabilities during the demo.

### Images

Three examples of broken hardware:

* Router displaying a red error light
* Smartphone with a cracked screen
* Frayed charging cable

### Documents

Two synthetic invoices:

* One valid warranty invoice
* One expired warranty invoice

### Expected Outcomes

Each fixture includes predefined expectations:

| Fixture          | Expected Intent | Expected Tool         |
| ---------------- | --------------- | --------------------- |
| Router Red Light | Troubleshooting | None                  |
| Valid Invoice    | Warranty Check  | check_warranty_status |
| Expired Invoice  | Warranty Check  | check_warranty_status |
| Unknown Error    | Escalation      | escalate_to_human     |

This ensures predictable behavior during live demonstrations.

An expired warranty does not automatically create an RMA. The agent explains that the product is out of warranty and offers human escalation unless a separate paid-repair or replacement policy is explicitly configured.

---

## Privacy Handling

Since cloud APIs are used during the hackathon, a local privacy layer is included.

### Privacy Masking Pipeline

Before sending uploaded documents to the model:

1. PDFs are rasterized locally into page images.
2. A local OCR step extracts text while preserving word-level bounding boxes.
3. Regex rules and named-entity recognition identify PII such as email addresses, phone numbers, names, and postal addresses.
4. OpenCV redacts the matching bounding boxes locally.
5. A verification step confirms that detected PII no longer appears in the sanitized OCR output.
6. Only sanitized page images and required product-related information are transmitted.

Examples of masked fields:

* Customer Name
* Home Address
* Phone Number
* Email Address

Retained fields:

* Product Name
* Model Number
* Purchase Date
* Serial Number

This minimizes exposure of personal data while preserving diagnostic utility.

---

# 4. Frontend UI (Streamlit)

The application consists of a dual-pane interface.

## User View (Pane 1)

Features:

* Chat-based conversation
* Drag-and-drop image upload
* PDF and invoice upload
* Real-time issue diagnosis
* Warranty verification
* RMA initiation

### Example User Flow

1. Upload photo of broken router.
2. Agent identifies model and issue.
3. Agent checks warranty.
4. Agent suggests troubleshooting steps.
5. Agent opens RMA if required.

---

## Admin View (Pane 2)

Powered by DuckDB analytics.

### Dashboard Metrics

* Total Sessions
* Active Users
* Resolution Rate
* Average Resolution Time
* Warranty Claims
* Open RMAs
* Human Escalations
* Sentiment Trends

### Example Visualizations

* Resolution Rate by Day
* Escalation Frequency
* Issue Type Distribution
* Warranty Claim Volume
* Average Response Time

---

# 5. Repository Structure

```text
visioncare/
│
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
│
├── backend/
│   ├── tools.py
│   ├── mock_db.json
│   ├── logger.py
│   ├── analytics.py
│   ├── manual_loader.py
│   └── privacy.py
│
├── data/
│   ├── manuals/
│   │   ├── manifest.json
│   │   └── router_troubleshooting.md
│   ├── audit_logs.jsonl
│   └── invoices/
│
├── tests/
│   └── fixtures/
│       ├── router_red_light.jpg
│       ├── cracked_screen.jpg
│       ├── frayed_cable.jpg
│       ├── valid_invoice.pdf
│       └── expired_invoice.pdf
│
└── frontend/
    ├── user_view.py
    └── admin_dashboard.py
```

---

# 6. Hackathon Value Proposition

### Why This Project Stands Out

* Multimodal support experience
* Vision-powered diagnostics
* OCR-enabled document processing
* Agentic tool-calling workflows
* Real-time operational analytics
* Privacy-aware preprocessing
* Fully demoable with mock services
* Lightweight architecture without vector databases

### Business Impact

Organizations can:

* Reduce support costs
* Improve first-contact resolution rates
* Accelerate warranty processing
* Increase customer satisfaction
* Gain actionable support analytics

---

# Demo Scenario

**User:** Uploads a photo of a router showing a red warning light and a warranty invoice.

**Agent Actions:**

1. Detects router model from image.
2. Reads invoice and extracts serial number.
3. Checks warranty status.
4. Determines issue severity.
5. Suggests troubleshooting.
6. Opens an RMA if troubleshooting fails.
7. Logs the interaction.
8. Updates the admin dashboard.

**Result:** End-to-end autonomous customer support powered by multimodal AI.
