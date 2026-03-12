# ChainFlow

AI-powered procurement and inventory management system for Indian manufacturing MSMEs.
Built on FastAPI, Azure SQL, Azure OpenAI, and React. Deployed on Azure App Service.

---

## What has been built

### Backend (FastAPI + Azure SQL)

- Multi-tenant inventory management with SKU tracking, reorder thresholds, and category-aware criticality scoring
- Rule-based alert engine with category multipliers (Raw Material, Components, Packaging) and lead-time factors
- Vendor management with scoring, delivery history, and SKU linkage
- Excel inventory upload with upsert logic and per-row error reporting
- Tally ERP sync listener that ingests stock ledger entries over HTTP
- Alembic migration history with 5 applied migrations including spend policies, delivery tables, quote records, and nullable vendor_id

### AI Agent (Azure OpenAI)

- Copilot chat endpoint backed by Azure OpenAI (GPT model) with tenant-aware context
- Autonomous reorder recommendations: agent scans inventory alerts, identifies the best vendor per SKU using scoring, and creates reorder records
- Spend policy enforcement: auto-approve for amounts under the configured threshold, escalation to owner for amounts above it
- Pending spend approval queue with approve and reject actions
- Supplier application pipeline: vendors submit applications, Meena (AI) evaluates and scores them, owner approves or rejects
- RFQ generation and send after approval
- AI-generated quote evaluation (Meena quote endpoint)
- Full recommendation history with status tracking

### Integrations

- Azure Communication Services (ACS) email: approval notifications, RFQ confirmations, PO emails, proforma invoices, spend approval requests
- Azure Blob Storage: document uploads for POs and invoices
- Azure Cosmos DB: agent audit logging for all AI decisions
- PO generator: creates structured purchase orders from approved recommendations
- Vendor simulator: local HTTP server that mimics vendor quote responses for testing

### Frontend (React + Vite + Tailwind)

- Dashboard with live inventory alerts, reorder recommendations, and order tracking
- Vendor comparison view with delivery history charts
- Spend analytics panel
- RFQ inbox with quote review and accept/reject flow
- Supplier applications panel with AI score display and approve/reject actions
- Copilot chat panel (ask questions about stock, vendors, spend)
- Health status indicator showing DB, Blob, and ACS status
- Approve and Send RFQ action in one step
- Built output (dist/) served directly by FastAPI as a static SPA

### Deployment

- GitHub Actions CI/CD workflow: builds React frontend, installs Python dependencies, deploys to Azure App Service on every push to main
- `startup.sh` for Azure App Service: locates gunicorn inside the Oryx-built virtual environment and starts the uvicorn worker
- CORS configured for both local development and `https://chainflow-app.azurewebsites.net`
- All Azure credentials stored as Azure App Service application settings (not in code)

---

## Local setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- ODBC Driver 18 for SQL Server (for Azure SQL connection)

### Install

```bash
git clone https://github.com/Deep-Axe/ai_unlocked.git
cd ai_unlocked/chainflow

python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r backend/requirements.txt
```

Copy the env template and fill in your Azure credentials:

```bash
copy .env.example .env    # Windows
cp .env.example .env      # macOS/Linux
```

### Build the frontend

```bash
cd frontend
npm install
npm run build
cd ..
```

### Run

```bash
cd chainflow
uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

API and React UI both served at `http://localhost:8000`.
Interactive API docs at `http://localhost:8000/docs`.

---

## Environment variables

See `chainflow/.env.example` for the full list. Required groups:

| Group        | Variables                                                                  |
| ------------ | -------------------------------------------------------------------------- |
| Database     | `DATABASE_URL`                                                             |
| Azure OpenAI | `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT` |
| ACS Email    | `ACS_CONNECTION_STRING`, `ACS_EMAIL_SENDER`, `TEST_EMAIL`, `OWNER_EMAIL`   |
| Azure Blob   | `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_CONTAINER`               |
| Azure Cosmos | `COSMOS_CONNECTION_STRING`, `COSMOS_DATABASE`, `COSMOS_CONTAINER`          |

---

## API reference

Interactive docs available at `/docs` when the server is running.

Key endpoint groups:

- `GET /health` - readiness probe for DB, Blob, and ACS
- `GET /inventory/alerts?tenant_id=1` - SKUs below reorder threshold, sorted by urgency
- `POST /inventory/upload/excel` - bulk upsert from Excel template
- `GET /vendors` - vendor list with scores
- `POST /agents/chat` - copilot chat
- `GET /agents/recommendations` - current reorder recommendations
- `POST /agents/recommendations/{id}/approve` - approve and trigger spend policy check
- `POST /agents/recommendations/{id}/send-rfq` - send RFQ email to vendor
- `GET /agents/spend-policy` - current spend approval tiers
- `GET /agents/rfq-inbox` - incoming quotes from vendors
- `POST /agents/supplier-applications/{id}/approve` - approve new supplier
- `GET /analytics/spend` - spend breakdown by category and vendor

---

## Database migrations

Migrations are managed with Alembic. To apply all migrations against Azure SQL:

```bash
cd chainflow
alembic upgrade head
```

To create a new migration after changing models:

```bash
alembic revision --autogenerate -m "description"
```

---

## ACS email note

The Azure-managed sender domain (`DoNotReply@xxx.azurecomm.net`) operates in sandbox mode by default. Recipient email addresses must be verified in the Azure portal under Communication Services before emails will be delivered. Go to the ACS resource in Azure portal, open Try Email, and add recipient addresses to the allowlist.

---

## Project structure

```
chainflow/
  backend/
    agents/          AI reorder agent
    integrations/    ACS email, Blob, Cosmos, Excel, PO generator, Tally listener
    routers/         API route handlers (agents, analytics, documents, health, inventory, vendors)
    scoring/         Vendor scoring and alert threshold logic
    main.py          FastAPI app entry point, also serves frontend dist/
    models.py        SQLAlchemy models
    schemas.py       Pydantic request/response schemas
  frontend/
    src/             React + Tailwind source (single App.jsx)
    dist/            Built output (served by FastAPI)
  alembic/           Migration scripts
  vendor_simulator/  Local vendor mock server for testing
  tally/             Tally TDL extension for HTTP sync
  .env.example       Environment variable template
```

No database server needed — SQLite is used locally for Week 1–2.

---

## Setup

```bash
git clone <repo>
cd chainflow

python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r backend/requirements.txt
```

Copy the example env file and edit as needed:

```bash
copy .env.example .env    # Windows
cp .env.example .env      # macOS/Linux
```

---

## Environment variables

| Variable              | Default                    | Required                 | Description                                     |
| --------------------- | -------------------------- | ------------------------ | ----------------------------------------------- |
| `DATABASE_URL`        | `sqlite:///./chainflow.db` | No                       | SQLAlchemy DB connection string                 |
| `TALLY_URL`           | `http://localhost:9000`    | No                       | Tally Prime HTTP server URL                     |
| `CHAINFLOW_API_URL`   | `http://localhost:8000`    | No                       | ChainFlow API base URL (used by tally_listener) |
| `CHAINFLOW_TENANT_ID` | —                          | **Yes** (for Tally sync) | Tenant ID to associate Tally data with          |

Create a `.env` file in the `chainflow/` root:

```
DATABASE_URL=sqlite:///./chainflow.db
TALLY_URL=http://localhost:9000
CHAINFLOW_API_URL=http://localhost:8000
CHAINFLOW_TENANT_ID=1
```

---

## Run the API

```bash
cd chainflow
uvicorn backend.main:app --reload
```

API is available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

---

## Seed data and testing alerts

Seed the database with one tenant (Harpreet Hosiery Works), 5 SKUs, and 3 vendors:

```bash
POST http://localhost:8000/dev/seed
```

Then immediately test the alert endpoint:

```bash
GET http://localhost:8000/inventory/alerts?tenant_id=1
```

Expected response — two alerts in urgency order:

```json
[
  {
    "sku_code": "DRAW-CORD-3MM",
    "stock_status": "critical",
    "current_quantity": 60,
    "reorder_threshold": 300
  },
  {
    "sku_code": "NYL-THREAD-40",
    "stock_status": "low",
    "current_quantity": 38,
    "reorder_threshold": 50
  }
]
```

`DRAW-CORD-3MM` is critical because `60 < 300 × 0.25 (Components multiplier) = 75`.
`NYL-THREAD-40` is low because `38 < 50` (below threshold) but above the critical level.

> **Lead-time note:** the seed data does not populate `lead_time_days` on vendor links,
> so all SKUs land in the ×1.0 lead-time band. The factors (×1.3 / ×1.0 / ×0.7) will
> become meaningful once Rohan fills in vendor lead times via `POST /vendors/{id}/link-sku`.

---

## Excel upload

1. Generate the template (only needed once):

   ```bash
   python sample_data/create_template.py
   ```

   This writes `sample_data/inventory_template.xlsx` with 8 hosiery rows and an
   Instructions sheet explaining every column.

2. Open the file, fill in your data, save.

3. Upload:
   ```bash
   POST http://localhost:8000/inventory/upload/excel?tenant_id=1
   Content-Type: multipart/form-data
   file: inventory_template.xlsx
   ```

The upload upserts rows keyed on `sku_code`. Existing SKUs get their quantity,
cost, and reorder thresholds updated. New SKU codes are created automatically.
Errors on individual rows are returned in the response body — they do not cancel
the rest of the upload.

---

## Tally sync

The sync runs as a standalone script on the same Windows machine as Tally Prime.

**Optional — load the visual verification report in Tally:**

1. Open Tally Prime → F12 → Product & Features → TDL Management
2. Add the path to `tally/chainflow.tdl`
3. Access via: Gateway of Tally → Display → Reports → ChainFlow Stock Verify

This report is for manual spot-checks only. The sync does not require TDL.

**Run the sync listener:**

```bash
cd chainflow
python -m backend.integrations.tally_listener
```

The listener runs one sync immediately on startup, then repeats every 5 minutes.
Logs are written to `backend/integrations/tally_sync.log` (append mode).

When a Tally item has no matching `sku_code` in ChainFlow, a new SKU is created
with category defaulting to `"Raw Material"`. The console will log:

```
New SKU created from Tally: NYLON-FITTING-12MM — category defaults to 'Raw Material', reclassify via PUT /inventory/skus/7
```

Reclassify via `PUT /inventory/skus/{id}` with `{"category": "Components"}`.

---

## Production notes

- `POST /dev/seed` and `GET /health` are not auth-gated in Week 1–2.
  Before any production deployment, guard `/dev/*` with an `ENV=development`
  check or remove the router entirely. These endpoints exist for demo and
  testing only.

- Multi-tenancy in Week 1–2 is enforced at the application layer via
  `tenant_id` query parameters. Database-level Row-Level Security is
  deferred to the Azure SQL migration in a later sprint.

- `@app.on_event("startup")` in `main.py` is deprecated in FastAPI 0.111+.
  The Week 3 sprint should migrate to the `lifespan` context manager pattern
  (the replacement code is commented inline in `main.py`).

---

## Threshold Logic & AI Roadmap

### Current: Rule-Based Threshold Engine (`backend/scoring/thresholds.py`)

Stock status (ok / low / critical) is currently computed using a deterministic
rule-based formula that factors in material category and vendor lead time:

    critical_threshold = reorder_threshold × category_multiplier × lead_time_factor

    Category multipliers:
      Raw Material  → 0.35  (production halts immediately without it)
      Components    → 0.25
      Packaging     → 0.15  (shorter lead times, more flexibility)

    Lead time factors:
      > 14 days  → 1.3  (warn earlier for long-lead materials)
      7–14 days  → 1.0
      < 7 days   → 0.7

This logic is transparent, explainable to Rohan, and requires zero training data.
It is the correct choice for a system that has not yet accumulated operational history.

### Planned: Agentic Reorder Intelligence (Phase 2)

The rule-based engine will be replaced by an Azure AI Foundry agent built on
Semantic Kernel. The agent will have access to two plugins:

- `get_consumption_rate` — queries InventoryLog for rolling 7/30-day
  consumption trends per SKU
- `get_vendor_lead_time` — pulls best available lead time from VendorSKULink

Instead of a static threshold comparison, the agent will reason:

    "Elastic yarn at 340kg. At current consumption rate you have 8 days of
    stock. Fastest vendor lead time is 11 days. Recommend reordering 500kg
    from Meena Sharma today."

This produces an explainable recommendation, not just an alert — which is
the core product promise of ChainFlow.
