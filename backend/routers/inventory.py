"""
ChainFlow — routers/inventory.py
Inventory management API endpoints.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 TODO — Step 6  (implement after main.py review is approved)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Endpoints to implement:

  GET    /inventory/skus
         List all SKUs for a tenant (tenant_id query param).
         Each SKU in response includes computed stock_status.

  GET    /inventory/skus/{sku_id}
         Single SKU detail. 404 if not found or wrong tenant.

  POST   /inventory/skus
         Create a new SKU manually (source="manual").

  PUT    /inventory/skus/{sku_id}
         Full update of a SKU. Preserve source unless explicitly changed.

  PATCH  /inventory/skus/{sku_id}/quantity
         Update ONLY current_quantity.
         Must write an InventoryLog row (change_source="manual_adjustment").
         Body: QuantityPatchRequest

  GET    /inventory/alerts
         Return all SKUs where current_quantity < reorder_threshold,
         sorted by (current_quantity / reorder_threshold) ascending
         (most critical first).

  POST   /inventory/upload/excel
         Accept multipart/form-data with an .xlsx file.
         Delegates parsing to excel_parser.parse_excel_upload().
         Returns {"created": int, "updated": int, "skipped": int, "errors": list[str]}

  POST   /inventory/sync/tally
         Accept TallySyncPayload JSON from tally_listener.py.
         Upsert each item: match on (tenant_id, sku_code).
           - If exists: update current_quantity, unit_cost, last_updated, source="tally"
             and write InventoryLog (change_source="tally_sync")
           - If not: create new SKU with source="tally",
             reorder_threshold=0, reorder_quantity=0 (Rohan fills later)
         Returns {"synced": int, "created": int, "updated": int}

Implementation notes:
  - Use get_db dependency injection on every endpoint
  - tenant_id is always a query param (no auth yet)
  - All 404s: {"error": "SKU not found", "detail": f"sku_id={sku_id}"}

  PUT /inventory/skus/{sku_id} — category change + critical_multiplier reset rule:
    compute_stock_status() reads sku.critical_multiplier from the DB, NOT a live
    CATEGORY_MULTIPLIERS lookup.  This is correct — it allows per-SKU tuning.
    But it creates one edge case:
        If category is being changed (e.g. "Components" → "Raw Material")
        AND critical_multiplier is NOT explicitly provided in the same request,
        auto-reset critical_multiplier to get_category_default_multiplier(new_category).
    If critical_multiplier IS explicitly provided, honour it — the caller knows
    what they're doing.
    Logic:
        new_category    = payload.category or sku.category
        new_multiplier  = payload.critical_multiplier
        if new_category != sku.category and new_multiplier is None:
            new_multiplier = get_category_default_multiplier(new_category)
"""

"""
ChainFlow — routers/inventory.py
Inventory management API endpoints.

Endpoints:
  GET    /inventory/skus                   list all SKUs (with stock_status)
  GET    /inventory/skus/{sku_id}          single SKU
  POST   /inventory/skus                   create SKU manually
  PUT    /inventory/skus/{sku_id}          full update (handles category-change multiplier reset)
  PATCH  /inventory/skus/{sku_id}/quantity update quantity + write InventoryLog
  GET    /inventory/alerts                 SKUs below reorder_threshold, most critical first
  POST   /inventory/upload/excel           parse + upsert from .xlsx upload
  POST   /inventory/sync/tally            upsert from tally_listener.py payload

Implementation notes:
  - Use get_db dependency injection on every endpoint
  - tenant_id is always a query param (no auth yet)
  - All 404s: {"error": "SKU not found", "detail": f"sku_id={sku_id}"}

  PUT /inventory/skus/{sku_id} — category change + critical_multiplier reset rule:
    compute_stock_status() reads sku.critical_multiplier from the DB, NOT a live
    CATEGORY_MULTIPLIERS lookup.  This is correct — it allows per-SKU tuning.
    But it creates one edge case:
        If category is being changed (e.g. "Components" → "Raw Material")
        AND critical_multiplier is NOT explicitly provided in the same request,
        auto-reset critical_multiplier to get_category_default_multiplier(new_category).
    If critical_multiplier IS explicitly provided, honour it — the caller knows
    what they're doing.
    Logic:
        new_category    = payload.category or sku.category
        new_multiplier  = payload.critical_multiplier
        if new_category != sku.category and new_multiplier is None:
            new_multiplier = get_category_default_multiplier(new_category)
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.integrations.excel_parser import parse_excel_upload
from backend.models import SKU, InventoryLog
from backend.schemas import (
    ExcelUploadSummary,
    QuantityPatchRequest,
    SKUCreate,
    SKUResponse,
    SKUUpdate,
    TallySyncPayload,
    TallySyncSummary,
)
from backend.scoring.thresholds import compute_stock_status, get_category_default_multiplier

router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _sku_to_response(sku: SKU) -> SKUResponse:
    """
    Convert a SKU ORM object to SKUResponse, populating stock_status via
    compute_stock_status() so category multipliers and vendor lead times are applied.

    vendor_links is already loaded by SQLAlchemy's lazy select when accessed —
    no explicit join needed here for single-SKU endpoints.
    """
    response = SKUResponse.model_validate(sku)
    response.stock_status = compute_stock_status(sku, sku.vendor_links)
    response.reorder_pending = bool(sku.reorder_pending)
    return response


def _get_sku_or_404(sku_id: int, tenant_id: int, db: Session) -> SKU:
    """Fetch a SKU by PK + tenant_id, raising 404 with structured JSON if missing."""
    sku = (
        db.query(SKU)
        .filter(SKU.id == sku_id, SKU.tenant_id == tenant_id)
        .first()
    )
    if not sku:
        raise HTTPException(
            status_code=404,
            detail={"error": "SKU not found", "detail": f"sku_id={sku_id}"},
        )
    return sku


def _write_inventory_log(
    db: Session,
    sku: SKU,
    new_quantity: float,
    change_source: str,
    notes: str | None = None,
) -> None:
    """Append one InventoryLog row. Always call before committing the quantity change."""
    log = InventoryLog(
        sku_id=sku.id,
        previous_quantity=sku.current_quantity,
        new_quantity=new_quantity,
        change_source=change_source,
        changed_at=datetime.utcnow(),
        notes=notes,
    )
    db.add(log)


# ──────────────────────────────────────────────────────────────────────────────
# GET /inventory/skus
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/skus", response_model=list[SKUResponse])
def list_skus(tenant_id: int, db: Session = Depends(get_db)) -> list[SKUResponse]:
    """
    List all SKUs for a tenant with real-time stock_status for each.

    stock_status is computed in-process from current_quantity, reorder_threshold,
    critical_multiplier, and the shortest vendor lead time — it is never stored
    in the DB, so this endpoint always reflects the current state.
    """
    skus = db.query(SKU).filter(SKU.tenant_id == tenant_id).all()
    return [_sku_to_response(sku) for sku in skus]


# ──────────────────────────────────────────────────────────────────────────────
# GET /inventory/skus/{sku_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/skus/{sku_id}", response_model=SKUResponse)
def get_sku(sku_id: int, tenant_id: int, db: Session = Depends(get_db)) -> SKUResponse:
    """
    Return a single SKU by ID.

    tenant_id is required to prevent cross-tenant data leakage — a request
    for sku_id=5 that belongs to tenant 2 returns 404 to tenant 1's caller.
    """
    sku = _get_sku_or_404(sku_id, tenant_id, db)
    return _sku_to_response(sku)


# ──────────────────────────────────────────────────────────────────────────────
# POST /inventory/skus
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/skus", response_model=SKUResponse, status_code=201)
def create_sku(
    payload: SKUCreate,
    tenant_id: int,
    db: Session = Depends(get_db),
) -> SKUResponse:
    """
    Create a new SKU manually.

    critical_multiplier is automatically set from the category default in
    scoring/thresholds.py.  It can be overridden later via PUT.

    Returns 409 if sku_code already exists for this tenant.
    """
    existing = (
        db.query(SKU)
        .filter(SKU.tenant_id == tenant_id, SKU.sku_code == payload.sku_code)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "SKU already exists",
                "detail": f"sku_code={payload.sku_code!r} already exists for tenant_id={tenant_id}",
            },
        )

    sku = SKU(
        tenant_id=tenant_id,
        last_updated=datetime.utcnow(),
        critical_multiplier=get_category_default_multiplier(payload.category),
        **payload.model_dump(),
    )
    db.add(sku)
    db.commit()
    db.refresh(sku)
    return _sku_to_response(sku)


# ──────────────────────────────────────────────────────────────────────────────
# PUT /inventory/skus/{sku_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.put("/skus/{sku_id}", response_model=SKUResponse)
def update_sku(
    sku_id: int,
    payload: SKUUpdate,
    tenant_id: int,
    db: Session = Depends(get_db),
) -> SKUResponse:
    """
    Full update of a SKU (partial — only fields present in the payload are written).

    Category-change + critical_multiplier reset rule:
        If category is changing AND critical_multiplier is not explicitly provided,
        critical_multiplier is reset to the new category's default.
        If critical_multiplier IS explicitly provided, that value wins regardless.
        This prevents a Raw Material SKU silently keeping a 0.15 Packaging multiplier
        after reclassification, while still honouring deliberate per-SKU tuning.

    Changing current_quantity via this endpoint does NOT write an InventoryLog row.
    Use PATCH /inventory/skus/{id}/quantity for audited quantity changes.
    """
    sku = _get_sku_or_404(sku_id, tenant_id, db)

    update_data = payload.model_dump(exclude_unset=True)

    # ── Category change → multiplier auto-reset ───────────────────────────────
    new_category = update_data.get("category", sku.category)
    new_multiplier = update_data.get("critical_multiplier")  # None if not in payload

    if new_category != sku.category and new_multiplier is None:
        # Category changed, multiplier not explicitly set — apply new default
        update_data["critical_multiplier"] = get_category_default_multiplier(new_category)

    for field, value in update_data.items():
        setattr(sku, field, value)

    sku.last_updated = datetime.utcnow()
    db.commit()
    db.refresh(sku)
    return _sku_to_response(sku)


# ──────────────────────────────────────────────────────────────────────────────
# PATCH /inventory/skus/{sku_id}/quantity
# ──────────────────────────────────────────────────────────────────────────────

@router.patch("/skus/{sku_id}/quantity", response_model=SKUResponse)
def update_quantity(
    sku_id: int,
    payload: QuantityPatchRequest,
    tenant_id: int,
    db: Session = Depends(get_db),
) -> SKUResponse:
    """
    Update a SKU's quantity and write an InventoryLog audit row.

    This is the ONLY endpoint that writes InventoryLog for manual adjustments.
    Always use this endpoint (not PUT) when the purpose is a stock count
    correction — Rohan and Harpreet need the paper trail.
    """
    sku = _get_sku_or_404(sku_id, tenant_id, db)

    _write_inventory_log(
        db,
        sku,
        new_quantity=payload.new_quantity,
        change_source="manual_adjustment",
        notes=payload.notes,
    )

    sku.current_quantity = payload.new_quantity
    sku.last_updated = datetime.utcnow()
    db.commit()
    db.refresh(sku)
    return _sku_to_response(sku)


# ──────────────────────────────────────────────────────────────────────────────
# GET /inventory/alerts
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/alerts", response_model=list[SKUResponse])
def get_alerts(tenant_id: int, db: Session = Depends(get_db)) -> list[SKUResponse]:
    """
    Return all SKUs where current_quantity < reorder_threshold, sorted most
    critical first (lowest stock ratio at the top).

    Sort key: current_quantity / reorder_threshold ascending.
    A SKU at 5% of threshold appears before one at 80% of threshold.

    Note: a SKU with reorder_threshold=0 (not yet configured) is never returned
    here — it would produce a division-by-zero in the sort and is not actionable.
    """
    skus = (
        db.query(SKU)
        .filter(
            SKU.tenant_id == tenant_id,
            SKU.reorder_threshold > 0,
            SKU.current_quantity < SKU.reorder_threshold,
        )
        .all()
    )

    # Sort in Python (not SQL) so we can use the ORM field values cleanly.
    skus.sort(key=lambda s: s.current_quantity / s.reorder_threshold)
    return [_sku_to_response(sku) for sku in skus]


# ──────────────────────────────────────────────────────────────────────────────
# POST /inventory/skus/{sku_id}/mark-received
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/skus/{sku_id}/mark-received")
def mark_stock_received(sku_id: int, tenant_id: int, db: Session = Depends(get_db)):
    """Mark a reorder as received — clears the reorder_pending flag on the SKU."""
    sku = db.query(SKU).filter(SKU.id == sku_id, SKU.tenant_id == tenant_id).first()
    if not sku:
        raise HTTPException(status_code=404, detail="SKU not found")
    sku.reorder_pending = False
    db.commit()
    return {"received": True, "sku_id": sku_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/upload/excel", response_model=ExcelUploadSummary)
async def upload_excel(
    tenant_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ExcelUploadSummary:
    """
    Upload an .xlsx inventory file and upsert its rows into the DB.

    Expects the column layout from sample_data/inventory_template.xlsx:
        sku_code | name | category | unit | current_quantity |
        reorder_threshold | reorder_quantity | unit_cost

    Returns a summary of rows created, updated, skipped, and any per-row errors.
    A 400 is raised only when the file is unreadable or missing required columns.
    Partial success (some rows error, others succeed) returns 200 with the
    errors list populated.
    """
    if not file.filename or not file.filename.endswith(".xlsx"):
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid file type", "detail": "Only .xlsx files are accepted"},
        )

    return await parse_excel_upload(file=file, tenant_id=tenant_id, db=db)


# ──────────────────────────────────────────────────────────────────────────────
# POST /inventory/sync/tally
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/sync/tally", response_model=TallySyncSummary)
def sync_tally(
    payload: TallySyncPayload,
    db: Session = Depends(get_db),
) -> TallySyncSummary:
    """
    Accept a stock snapshot from tally_listener.py and upsert each item.

    Upsert logic (keyed on tenant_id + sku_code):
        EXISTS → update current_quantity, unit, last_updated, source="tally"
                 write InventoryLog (change_source="tally_sync")
                 Only logs if quantity actually changed — avoids polluting the
                 audit trail with no-op syncs every 5 minutes.

        NEW    → create SKU with source="tally"
                 reorder_threshold=0, reorder_quantity=0 (Rohan configures later)
                 critical_multiplier set from category default if category can be
                 inferred, otherwise 0.20 fallback

    tenant_id comes from the payload body (not a query param) — the listener
    script knows its tenant from CHAINFLOW_TENANT_ID env var and embeds it.
    """
    created = 0
    updated = 0

    for item in payload.items:
        existing: SKU | None = (
            db.query(SKU)
            .filter(SKU.tenant_id == payload.tenant_id, SKU.sku_code == item.sku_code)
            .first()
        )

        if existing:
            # Only write a log row if the quantity actually changed.
            if existing.current_quantity != item.quantity:
                _write_inventory_log(
                    db,
                    existing,
                    new_quantity=item.quantity,
                    change_source="tally_sync",
                )
                existing.current_quantity = item.quantity

            existing.unit = item.unit
            existing.last_updated = datetime.utcnow()
            existing.source = "tally"
            updated += 1
        else:
            new_sku = SKU(
                tenant_id=payload.tenant_id,
                sku_code=item.sku_code,
                name=item.name,
                # Tally does not carry category — default to "Raw Material".
                # Rohan can reclassify via PUT /inventory/skus/{id}.
                category="Raw Material",
                unit=item.unit,
                current_quantity=item.quantity,
                reorder_threshold=0.0,
                reorder_quantity=0.0,
                unit_cost=0.0,
                critical_multiplier=get_category_default_multiplier("Raw Material"),
                last_updated=datetime.utcnow(),
                source="tally",
            )
            db.add(new_sku)
            # Flush to get the auto-assigned id before logging
            db.flush()
            import logging as _logging
            _logging.getLogger("chainflow").info(
                "New SKU created from Tally: %s — category defaults to "
                "'Raw Material', reclassify via PUT /inventory/skus/%d",
                new_sku.sku_code,
                new_sku.id,
            )
            created += 1

    db.commit()

    return TallySyncSummary(
        synced=len(payload.items),
        created=created,
        updated=updated,
    )


# ──────────────────────────────────────────────────────────────────────────────
# GET /inventory/logs
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/logs")
def get_inventory_logs(
    tenant_id: int,
    sku_id: int,
    days: int = 30,
    db: Session = Depends(get_db),
):
    """
    Inventory quantity history for a single SKU over the last N days.
    Used for the stock trend line chart on alert cards.

    Returns [{date: ISO string, quantity: float}] sorted oldest → newest.
    """
    since = datetime.utcnow() - timedelta(days=days)
    logs = (
        db.query(InventoryLog)
        .filter(
            InventoryLog.sku_id == sku_id,
            InventoryLog.changed_at >= since,
        )
        .order_by(InventoryLog.changed_at.asc())
        .all()
    )
    return [
        {"date": log.changed_at.isoformat(), "quantity": log.new_quantity}
        for log in logs
    ]
