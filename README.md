# ChainFlow

Inventory management backend for small Indian manufacturers.
Connects to Tally ERP, accepts Excel uploads, and surfaces reorder alerts
with vendor recommendations.

**Current state:** Week 1–2 complete — REST API, rule-based alert engine,
Tally sync listener, Excel upload, vendor management.
Week 3 (Azure AI Foundry agentic reorder intelligence) is planned.

---

## Prerequisites

- Python 3.11+
- pip

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

## ⚠️ Production notes

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
