"""
ChainFlow — routers/vendors.py
Vendor management API endpoints.

Route registration order — IMPORTANT:
  GET /vendors/for-sku/{sku_id}  ← registered FIRST (line ~60)
  GET /vendors/{vendor_id}       ← registered SECOND (line ~90)

  FastAPI matches routes in declaration order. Registering {vendor_id} first
  would cause /vendors/for-sku/5 to attempt casting "for-sku" as an integer,
  returning a 422 instead of the supplier list — a silent runtime bug.

Endpoints:
  GET    /vendors                          list all vendors for a tenant
  GET    /vendors/for-sku/{sku_id}         vendors who supply a SKU (score DESC) ← FIRST
  GET    /vendors/{vendor_id}              single vendor with linked SKUs         ← SECOND
  POST   /vendors                          create vendor
  PUT    /vendors/{vendor_id}              update vendor (score excluded)
  POST   /vendors/{vendor_id}/link-sku     create VendorSKULink (409 if duplicate)
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import SKU, Vendor, VendorSKULink
from backend.schemas import (
    DeliveryRecord,
    VendorCreate,
    VendorResponse,
    VendorSKULinkCreate,
    VendorSKULinkResponse,
    VendorUpdate,
)
from backend.scoring.vendor_scorer import compute_vendor_score

router = APIRouter()


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _get_vendor_or_404(vendor_id: int, tenant_id: int, db: Session) -> Vendor:
    """
    Fetch a Vendor by PK, enforcing tenant isolation.

    A vendor that exists but belongs to a different tenant returns 404 —
    not 403 — so callers cannot infer whether a vendor_id exists in other tenants.
    """
    vendor = (
        db.query(Vendor)
        .filter(Vendor.id == vendor_id, Vendor.tenant_id == tenant_id)
        .first()
    )
    if not vendor:
        raise HTTPException(
            status_code=404,
            detail={"error": "Vendor not found", "detail": f"vendor_id={vendor_id}"},
        )
    return vendor


# ──────────────────────────────────────────────────────────────────────────────
# GET /vendors
# ──────────────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[VendorResponse])
def list_vendors(tenant_id: int, db: Session = Depends(get_db)) -> list[VendorResponse]:
    """
    List all vendors for a tenant.

    Each response includes the derived on_time_rate (computed in VendorResponse
    as a @computed_field from total_orders / on_time_deliveries).
    sku_links is populated via SQLAlchemy lazy load — one extra query per vendor.
    For Week 1-2 scale (tens of vendors) this is fine; use joinedload for
    larger datasets in a future sprint.
    """
    vendors = db.query(Vendor).filter(Vendor.tenant_id == tenant_id).all()
    return [VendorResponse.model_validate(v) for v in vendors]


# ──────────────────────────────────────────────────────────────────────────────
# GET /vendors/for-sku/{sku_id}   ← MUST be before GET /vendors/{vendor_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/for-sku/{sku_id}", response_model=list[VendorResponse])
def get_vendors_for_sku(
    sku_id: int,
    tenant_id: int,
    db: Session = Depends(get_db),
) -> list[VendorResponse]:
    """
    Return all vendors who can supply a given SKU, sorted by score descending.

    This is the primary endpoint Rohan uses when an alert fires — he can see
    immediately which vendor to contact first based on their performance score.

    Verifies the SKU belongs to the tenant before querying vendor links, so
    cross-tenant sku_ids return 404 rather than leaking vendor data.
    """
    # Confirm the SKU belongs to this tenant
    sku = db.query(SKU).filter(SKU.id == sku_id, SKU.tenant_id == tenant_id).first()
    if not sku:
        raise HTTPException(
            status_code=404,
            detail={"error": "SKU not found", "detail": f"sku_id={sku_id}"},
        )

    vendors = (
        db.query(Vendor)
        .join(VendorSKULink, VendorSKULink.vendor_id == Vendor.id)
        .filter(
            VendorSKULink.sku_id == sku_id,
            Vendor.tenant_id == tenant_id,
        )
        .order_by(Vendor.score.desc())
        .all()
    )
    return [VendorResponse.model_validate(v) for v in vendors]


# ──────────────────────────────────────────────────────────────────────────────
# GET /vendors/{vendor_id}   ← MUST be after GET /vendors/for-sku/{sku_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/{vendor_id}", response_model=VendorResponse)
def get_vendor(
    vendor_id: int,
    tenant_id: int,
    db: Session = Depends(get_db),
) -> VendorResponse:
    """
    Return a single vendor with their full VendorSKULink list.

    sku_links is lazy-loaded by SQLAlchemy when VendorResponse accesses
    vendor.sku_links during serialisation — no explicit join needed.
    """
    vendor = _get_vendor_or_404(vendor_id, tenant_id, db)
    return VendorResponse.model_validate(vendor)


# ──────────────────────────────────────────────────────────────────────────────
# POST /vendors
# ──────────────────────────────────────────────────────────────────────────────

@router.post("", response_model=VendorResponse, status_code=201)
def create_vendor(
    payload: VendorCreate,
    tenant_id: int,
    db: Session = Depends(get_db),
) -> VendorResponse:
    """
    Create a new vendor.

    score is NOT in VendorCreate — the DB default of 50.0 (neutral baseline)
    is applied automatically.  The Week 3 scoring algorithm is the only path
    that updates score based on actual transaction outcomes.

    total_orders and on_time_deliveries also start at 0 via DB defaults.
    """
    vendor = Vendor(
        tenant_id=tenant_id,
        created_at=datetime.utcnow(),
        **payload.model_dump(),
    )
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return VendorResponse.model_validate(vendor)


# ──────────────────────────────────────────────────────────────────────────────
# PUT /vendors/{vendor_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.put("/{vendor_id}", response_model=VendorResponse)
def update_vendor(
    vendor_id: int,
    payload: VendorUpdate,
    tenant_id: int,
    db: Session = Depends(get_db),
) -> VendorResponse:
    """
    Update vendor contact details and operational notes.

    score, total_orders, and on_time_deliveries are intentionally excluded
    from VendorUpdate — they are managed by the Week 3 scoring algorithm.
    Allowing manual edits here would corrupt the performance baseline that
    Harpreet and Rohan rely on for procurement decisions.
    """
    vendor = _get_vendor_or_404(vendor_id, tenant_id, db)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(vendor, field, value)

    db.commit()
    db.refresh(vendor)
    return VendorResponse.model_validate(vendor)


# ──────────────────────────────────────────────────────────────────────────────
# POST /vendors/{vendor_id}/link-sku
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/{vendor_id}/link-sku", response_model=VendorSKULinkResponse, status_code=201)
def link_sku_to_vendor(
    vendor_id: int,
    payload: VendorSKULinkCreate,
    tenant_id: int,
    db: Session = Depends(get_db),
) -> VendorSKULinkResponse:
    """
    Link a vendor to a SKU they can supply, optionally with price and lead time.

    Both the vendor and the SKU must belong to the same tenant.
    Returns 409 if the link already exists — use a future PATCH endpoint
    to update quoted_price or lead_time_days on an existing link.
    """
    # Verify vendor belongs to tenant
    vendor = _get_vendor_or_404(vendor_id, tenant_id, db)

    # Verify SKU belongs to the same tenant
    sku = (
        db.query(SKU)
        .filter(SKU.id == payload.sku_id, SKU.tenant_id == tenant_id)
        .first()
    )
    if not sku:
        raise HTTPException(
            status_code=404,
            detail={"error": "SKU not found", "detail": f"sku_id={payload.sku_id}"},
        )

    # Check for duplicate link
    existing_link = (
        db.query(VendorSKULink)
        .filter(
            VendorSKULink.vendor_id == vendor.id,
            VendorSKULink.sku_id == sku.id,
        )
        .first()
    )
    if existing_link:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "Link already exists",
                "detail": f"vendor_id={vendor_id} sku_id={payload.sku_id}",
            },
        )

    link = VendorSKULink(
        vendor_id=vendor.id,
        sku_id=sku.id,
        quoted_price=payload.quoted_price,
        lead_time_days=payload.lead_time_days,
    )
    db.add(link)
    db.commit()
    db.refresh(link)
    return VendorSKULinkResponse.model_validate(link)


# ──────────────────────────────────────────────────────────────────────────────
# POST /vendors/{vendor_id}/record-delivery
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/{vendor_id}/record-delivery", response_model=VendorResponse)
def record_delivery(
    vendor_id: int,
    payload: DeliveryRecord,
    tenant_id: int,
    db: Session = Depends(get_db),
) -> VendorResponse:
    """
    Record the outcome of a single delivery and recompute the vendor score.

    Each call:
      1. Increments total_orders by 1.
      2. If was_on_time is True → increments on_time_deliveries by 1.
      3. If had_quality_issue is True → increments quality_issues by 1.
      4. Recomputes score via compute_vendor_score() and saves it.

    Returns the updated VendorResponse so the caller can see the new score
    immediately without a second GET request.
    """
    vendor = _get_vendor_or_404(vendor_id, tenant_id, db)

    vendor.total_orders += 1
    if payload.was_on_time:
        vendor.on_time_deliveries += 1
    if payload.had_quality_issue:
        vendor.quality_issues += 1

    vendor.score = compute_vendor_score(
        vendor.total_orders,
        vendor.on_time_deliveries,
        vendor.quality_issues,
    )

    db.commit()
    db.refresh(vendor)
    return VendorResponse.model_validate(vendor)


# ──────────────────────────────────────────────────────────────────────────────
# GET /vendors/{vendor_id}/delivery-history
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/{vendor_id}/delivery-history")
def get_delivery_history(
    vendor_id: int,
    tenant_id: int,
    db: Session = Depends(get_db),
):
    """
    Last 25 delivery records for a vendor.
    Used for the delivery history dots in Harpreet's Vendor Health tab
    and Meena's My Performance tab.
    """
    from backend.models import DeliveryRecord as DeliveryRecordORM
    records = (
        db.query(DeliveryRecordORM)
        .filter(
            DeliveryRecordORM.vendor_id == vendor_id,
            DeliveryRecordORM.tenant_id == tenant_id,
        )
        .order_by(DeliveryRecordORM.delivered_at.desc())
        .limit(25)
        .all()
    )
    return [
        {
            "delivered_at": r.delivered_at.isoformat(),
            "was_on_time": r.was_on_time,
            "had_quality_issue": r.had_quality_issue,
            "notes": r.notes,
        }
        for r in records
    ]
