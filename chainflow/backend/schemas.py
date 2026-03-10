"""
ChainFlow — schemas.py
Pydantic v2 request/response schemas for all API endpoints.

Naming convention:
    <Model>Create    — request body for POST (creation)
    <Model>Update    — request body for PUT  (full replacement; all fields optional
                       so callers only send what they want to change)
    <Model>Response  — response body for GET / POST / PUT

All Response schemas set model_config = ConfigDict(from_attributes=True)
so they can be constructed directly from SQLAlchemy ORM objects:
    SKUResponse.model_validate(sku_orm_object)
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator, computed_field


# ──────────────────────────────────────────────────────────────────────────────
# Tenant
# ──────────────────────────────────────────────────────────────────────────────

class TenantResponse(BaseModel):
    """Read-only view of a Tenant.  Tenants are created only via /dev/seed for now."""

    id: int
    name: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────────────────────────────────────────
# SKU
# ──────────────────────────────────────────────────────────────────────────────

class SKUCreate(BaseModel):
    """
    Body for POST /inventory/skus.

    tenant_id is NOT in this schema — it is always taken from the query param
    so it can never be accidentally spoofed in the request body.
    source defaults to "manual" here; tally/excel sync endpoints override it
    in the router before writing to the DB.
    """

    sku_code: str
    name: str
    category: str
    unit: str
    current_quantity: float = 0.0
    reorder_threshold: float = 0.0
    reorder_quantity: float = 0.0
    unit_cost: float = 0.0
    source: str = "manual"


class SKUUpdate(BaseModel):
    """
    Body for PUT /inventory/skus/{sku_id}.

    Every field is optional — only supplied fields overwrite the stored value.
    Note: changing current_quantity via this endpoint does NOT write an
    InventoryLog row.  Use PATCH /inventory/skus/{id}/quantity for that.
    source should only be set if the caller is explicitly re-flagging the origin.
    """

    name: str | None = None
    category: str | None = None
    unit: str | None = None
    current_quantity: float | None = None
    reorder_threshold: float | None = None
    reorder_quantity: float | None = None
    unit_cost: float | None = None
    source: str | None = None
    # Allow per-SKU threshold tuning without changing the category.
    critical_multiplier: float | None = None


class SKUResponse(BaseModel):
    """
    Full SKU read response, including the computed stock_status field.

    stock_status logic (evaluated from in-memory values, not a DB column):
        "critical"  — current_quantity < 20% of reorder_threshold
                      → production will halt within days; escalate to Harpreet
        "low"       — current_quantity < reorder_threshold
                      → Rohan should place a reorder now
        "ok"        — stock is above threshold; no action needed

    Edge case: if reorder_threshold == 0 (not yet configured), status is "ok"
    regardless of quantity.  Rohan needs to fill in the threshold first.
    """

    id: int
    tenant_id: int
    sku_code: str
    name: str
    category: str
    unit: str
    current_quantity: float
    reorder_threshold: float
    reorder_quantity: float
    unit_cost: float
    critical_multiplier: float
    last_updated: datetime
    source: str

    # ── stock_status ──────────────────────────────────────────────────────────
    # Previously a @computed_field using a hardcoded 20% rule.
    # Moved to the router layer so compute_stock_status() in
    # backend/scoring/thresholds.py can apply per-category multipliers and
    # vendor lead-time factors.  Every endpoint that returns an SKUResponse
    # MUST set this explicitly via:
    #     from backend.scoring.thresholds import compute_stock_status
    #     stock_status=compute_stock_status(sku_orm, sku_orm.vendor_links)
    # The "ok" default below is a safety sentinel only — do not rely on it.
    stock_status: str = "ok"
    reorder_pending: bool = False

    @field_validator("reorder_pending", mode="before")
    @classmethod
    def coerce_bool(cls, v: object) -> bool:
        return bool(v) if v is not None else False

    model_config = ConfigDict(from_attributes=True)


class QuantityPatchRequest(BaseModel):
    """
    Body for PATCH /inventory/skus/{sku_id}/quantity.

    This is the ONLY way to update quantity that also writes an InventoryLog row.
    The router reads the SKU's current quantity before writing, so it can
    populate previous_quantity in the log.
    """

    new_quantity: float
    notes: str | None = None


# ──────────────────────────────────────────────────────────────────────────────
# VendorSKULink  (defined before VendorResponse so the forward ref resolves)
# ──────────────────────────────────────────────────────────────────────────────

class VendorSKULinkCreate(BaseModel):
    """
    Body for POST /vendors/{vendor_id}/link-sku.

    sku_id is the only required field — price and lead time can be filled
    later once a formal quote is received.
    """

    sku_id: int
    quoted_price: float | None = None
    lead_time_days: int | None = None


class VendorSKULinkResponse(BaseModel):
    """Read view of a single Vendor↔SKU link row."""

    id: int
    vendor_id: int
    sku_id: int
    quoted_price: float | None
    lead_time_days: int | None

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────────────────────────────────────────
# Vendor
# ──────────────────────────────────────────────────────────────────────────────

class VendorCreate(BaseModel):
    """
    Body for POST /vendors.

    score, total_orders, on_time_deliveries are intentionally absent — the DB
    sets them to their defaults (50.0, 0, 0).  The scoring algorithm (Week 3)
    is the only thing that should update score.
    """

    name: str
    contact_name: str
    phone: str
    email: str | None = None
    city: str
    materials_supplied: str  # e.g. "Raw Material,Components"
    notes: str | None = None


class VendorUpdate(BaseModel):
    """
    Body for PUT /vendors/{vendor_id}.

    ⚠️  score is NOT included here — it is updated only by the scoring algorithm
    in Week 3.  Allowing manual score edits would corrupt the performance data.
    total_orders and on_time_deliveries are also excluded for the same reason.
    """

    name: str | None = None
    contact_name: str | None = None
    phone: str | None = None
    email: str | None = None
    city: str | None = None
    materials_supplied: str | None = None
    notes: str | None = None
    quality_issues: int | None = None


class DeliveryRecord(BaseModel):
    """
    Body for POST /vendors/{vendor_id}/record-delivery.

    Captures the outcome of a single delivery event.  Each call increments
    total_orders by 1, conditionally increments on_time_deliveries and
    quality_issues, then recomputes vendor.score via compute_vendor_score().
    """

    was_on_time: bool
    had_quality_issue: bool
    notes: str | None = None


class VendorResponse(BaseModel):
    """
    Full vendor read response, including linked SKUs and derived on_time_rate.

    on_time_rate:
        None      — vendor has no recorded orders yet (avoids showing "0%"
                    which implies a history of late deliveries)
        0.0–100.0 — percentage of orders delivered on time, 1 decimal place

    sku_links is populated when the endpoint joins VendorSKULink rows.
    On list endpoints (GET /vendors) it may be an empty list if not joined.
    """

    id: int
    tenant_id: int
    name: str
    contact_name: str
    phone: str
    email: str | None
    city: str
    materials_supplied: str
    score: float
    total_orders: int
    on_time_deliveries: int
    quality_issues: int
    created_at: datetime
    notes: str | None
    sku_links: list[VendorSKULinkResponse] = []

    @computed_field  # type: ignore[misc]
    @property
    def on_time_rate(self) -> float | None:
        """On-time delivery percentage; None when no orders have been recorded yet."""
        if self.total_orders == 0:
            return None
        return round(self.on_time_deliveries / self.total_orders * 100, 1)

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────────────────────────────────────────
# InventoryLog
# ──────────────────────────────────────────────────────────────────────────────

class InventoryLogResponse(BaseModel):
    """
    Read view of a single InventoryLog row.

    Logs are append-only — there is no Create/Update schema for this model.
    New rows are written internally by the router when quantity changes.
    """

    id: int
    sku_id: int
    previous_quantity: float
    new_quantity: float
    change_source: str      # "tally_sync" | "excel_upload" | "manual_adjustment"
    changed_at: datetime
    notes: str | None

    model_config = ConfigDict(from_attributes=True)


# ──────────────────────────────────────────────────────────────────────────────
# Tally sync payload  (sent by tally_listener.py → POST /inventory/sync/tally)
# ──────────────────────────────────────────────────────────────────────────────

class TallyStockItem(BaseModel):
    """
    A single stock item extracted from Tally's XML export.

    sku_code is derived from the Tally stock item name by tally_listener.py:
        uppercase → replace spaces with "-" → strip punctuation
    e.g. "Nylon Fitting 12mm" → "NYLON-FITTING-12MM"

    quantity is the CLOSING BALANCE (not a delta) — the listener always
    sends the absolute current stock level, not a change.  The router
    computes the delta and writes the InventoryLog entry.
    """

    sku_code: str
    name: str
    quantity: float
    unit: str


class TallySyncPayload(BaseModel):
    """
    Full payload POSTed to POST /inventory/sync/tally by tally_listener.py.

    tenant_id is included so the endpoint can upsert under the correct tenant
    without relying on a query param — the listener script knows its tenant
    from the CHAINFLOW_TENANT_ID env var.
    """

    tenant_id: int
    items: list[TallyStockItem]


# ──────────────────────────────────────────────────────────────────────────────
# Excel upload response  (returned by POST /inventory/upload/excel)
# ──────────────────────────────────────────────────────────────────────────────

class ExcelUploadSummary(BaseModel):
    """
    Summary returned after processing an Excel upload.

    errors contains human-readable per-row messages, e.g.:
        ["Row 5: unit_cost 'N/A' is not a valid number"]
    An upload that produces errors is still a 200 — partial success is valid.
    A 400 is only raised when the file itself is unreadable or missing columns.
    """

    created: int
    updated: int
    skipped: int
    errors: list[str] = []


# ──────────────────────────────────────────────────────────────────────────────
# Tally sync response  (returned by POST /inventory/sync/tally)
# ──────────────────────────────────────────────────────────────────────────────

class TallySyncSummary(BaseModel):
    """Summary returned after processing a Tally sync payload."""

    synced: int     # total items received in the payload
    created: int    # new SKU rows created (first time this sku_code seen)
    updated: int    # existing SKU rows whose quantity was updated


# ──────────────────────────────────────────────────────────────────────────────
# ReorderRecommendation
# ──────────────────────────────────────────────────────────────────────────────

class ReorderRecommendationResponse(BaseModel):
    """
    Read response for a single AI-generated reorder recommendation.

    sku_code and vendor_name are NOT ORM columns — they are populated
    manually in the router by reading the related SKU and Vendor rows:
        response.sku_code = recommendation.sku.sku_code
        response.vendor_name = recommendation.vendor.name
    This avoids a full nested serialisation of SKUResponse/VendorResponse
    while still giving the UI the two human-readable identifiers it needs.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    tenant_id: int
    sku_id: int
    vendor_id: int
    quantity: float
    reasoning: str
    status: str
    created_at: datetime
    approved_at: datetime | None
    sms_sent: bool
    sku_code: str | None = None      # populated manually in router, not from ORM
    vendor_name: str | None = None   # populated manually in router, not from ORM
