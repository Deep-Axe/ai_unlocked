"""
ChainFlow — main.py
FastAPI application entry point, lifecycle management, and dev utilities.

Running locally:
    uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

Quick smoke test after a fresh install:
    POST /dev/seed          → seeds tenant + 5 SKUs + 3 vendors
    GET  /inventory/alerts  → returns DRAW-CORD-3MM (critical) + NYL-THREAD-40 (low)
    GET  /health            → {"status": "ok", "db": "connected"}
"""

import os
from datetime import datetime

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

load_dotenv()

from backend.database import Base, engine, get_db
from backend.models import SKU, Tenant, Vendor, VendorSKULink
from backend.routers import agents as agents_router
from backend.routers import analytics as analytics_router
from backend.routers import documents as documents_router
from backend.routers import inventory as inventory_router
from backend.routers import vendors as vendors_router
from backend.routers.health import router as health_router
from backend.scoring.thresholds import get_category_default_multiplier

# ──────────────────────────────────────────────────────────────────────────────
# Application
# ──────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ChainFlow API",
    description=(
        "Intelligent supply chain copilot for Indian manufacturing MSMEs. "
        "Week 1-2: structured inventory ingestion via Tally ERP and Excel."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ──────────────────────────────────────────────────────────────────────────────
# CORS — localhost dev origins only (tighten before cloud deploy)
# ──────────────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",   # Create React App
        "http://localhost:5173",   # Vite
        "http://localhost:8080",   # Generic dev server
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ──────────────────────────────────────────────────────────────────────────────
# Startup
# ──────────────────────────────────────────────────────────────────────────────
#
# TODO (Week 3 cleanup): @app.on_event("startup") was soft-deprecated in
# FastAPI 0.93+.  With fastapi==0.111.0 it still works fine and produces only
# a DeprecationWarning.  Migrate to the lifespan pattern before cloud deploy:
#
#     from contextlib import asynccontextmanager
#
#     @asynccontextmanager
#     async def lifespan(app: FastAPI):
#         Base.metadata.create_all(bind=engine)
#         yield                          # app runs here
#         # add shutdown logic below yield if needed
#
#     app = FastAPI(lifespan=lifespan, ...)
#
@app.on_event("startup")
def create_tables() -> None:
    """Create all SQLAlchemy-managed tables on first boot if they don't exist.
    Wrapped in try/except so a transient DB firewall block doesn't crash the
    server process — endpoints will still fail until the DB is reachable, but
    uvicorn stays alive and can be hit once the firewall propagates.
    """
    try:
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        import logging as _log
        _log.getLogger("chainflow").warning(
            "DB unreachable at startup (firewall?): %s — server starting anyway", exc
        )


# ──────────────────────────────────────────────────────────────────────────────
# Routers
# ──────────────────────────────────────────────────────────────────────────────

app.include_router(inventory_router.router, prefix="/inventory",  tags=["inventory"])
app.include_router(vendors_router.router,   prefix="/vendors",    tags=["vendors"])
app.include_router(agents_router.router,    prefix="/agents",     tags=["agents"])
app.include_router(analytics_router.router, prefix="/analytics",  tags=["analytics"])
app.include_router(documents_router.router, prefix="/documents",  tags=["documents"])
app.include_router(health_router,           tags=["ops"])  # serves at /health

# ──────────────────────────────────────────────────────────────────────────────
# Frontend — serve built Vite/React SPA from FastAPI
# Mounted AFTER all API routers so /docs and /api/* take priority.
# ──────────────────────────────────────────────────────────────────────────────

_FRONTEND_DIST = os.path.join(
    os.path.dirname(__file__), "..", "frontend", "dist"
)

if os.path.isdir(_FRONTEND_DIST):
    # /assets/*, /favicon.ico etc.
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(_FRONTEND_DIST, "assets")),
        name="assets",
    )

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str = "") -> FileResponse:
        """Return index.html for every non-API path (SPA client-side routing)."""
        # Serve specific files that exist inside dist (e.g. favicon.ico)
        candidate = os.path.join(_FRONTEND_DIST, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_FRONTEND_DIST, "index.html"))


# ──────────────────────────────────────────────────────────────────────────────
# Seed data
# ──────────────────────────────────────────────────────────────────────────────
#
# 5 SKUs are spread across all three categories so thresholds.py logic is
# testable immediately after seeding.  Expected statuses (no vendor links yet,
# so lead_time_factor=1.0 for all):
#
#   DRAW-CORD-3MM   Components  qty=60   threshold=300  critical=75    → critical
#   NYL-THREAD-40   Raw Mat.    qty=38   threshold=50   critical=17.5  → low
#   ELAS-YARN-20MM  Raw Mat.    qty=850  threshold=500  critical=175   → ok
#   NYL-FIT-12MM    Components  qty=4800 threshold=1000 critical=250   → ok
#   PKG-POLY-BAG-M  Packaging   qty=9500 threshold=2000 critical=300   → ok
#
# GET /inventory/alerts?tenant_id=1  should return DRAW-CORD-3MM + NYL-THREAD-40.

_SEED_SKUS: list[dict] = [
    {
        "sku_code": "ELAS-YARN-20MM",
        "name": "Elastic Yarn 20mm Width",
        "category": "Raw Material",
        "unit": "metres",
        "current_quantity": 850.0,
        "reorder_threshold": 500.0,
        "reorder_quantity": 2000.0,
        "unit_cost": 3.50,
    },
    {
        "sku_code": "NYL-THREAD-40",
        "name": "Nylon Thread 40 Denier",
        "category": "Raw Material",
        "unit": "kg",
        "current_quantity": 38.0,
        "reorder_threshold": 50.0,
        "reorder_quantity": 200.0,
        "unit_cost": 280.00,
    },
    {
        "sku_code": "NYL-FIT-12MM",
        "name": "Nylon Fitting 12mm",
        "category": "Components",
        "unit": "units",
        "current_quantity": 4800.0,
        "reorder_threshold": 1000.0,
        "reorder_quantity": 5000.0,
        "unit_cost": 1.20,
    },
    {
        "sku_code": "DRAW-CORD-3MM",
        "name": "Drawcord 3mm Round",
        "category": "Components",
        "unit": "metres",
        "current_quantity": 60.0,
        "reorder_threshold": 300.0,
        "reorder_quantity": 1500.0,
        "unit_cost": 2.20,
    },
    {
        "sku_code": "PKG-POLY-BAG-M",
        "name": "Polybag Medium 12x18 inch",
        "category": "Packaging",
        "unit": "units",
        "current_quantity": 9500.0,
        "reorder_threshold": 2000.0,
        "reorder_quantity": 10000.0,
        "unit_cost": 0.80,
    },
]

_SEED_VENDORS: list[dict] = [
    {
        "name": "Sharma Textiles",
        "contact_name": "Rajesh Sharma",
        "phone": "+91-98140-11111",
        "email": None,
        "city": "Ludhiana",
        "materials_supplied": "Raw Material",
        "notes": "Primary yarn and thread supplier. Typical lead time 10-12 days.",
    },
    {
        "name": "Punjab Components House",
        "contact_name": "Balwinder Singh",
        "phone": "+91-98150-22222",
        "email": "bsing@pchouse.in",
        "city": "Jalandhar",
        "materials_supplied": "Components",
        "notes": "Fittings and drawcord hardware. Minimum order quantities apply.",
    },
    {
        "name": "Gupta Packaging Works",
        "contact_name": "Amit Gupta",
        "phone": "+91-98760-33333",
        "email": None,
        "city": "Ludhiana",
        "materials_supplied": "Packaging",
        "notes": "Polybags and labels. Bulk pricing above 10,000 units.",
    },
]


@app.post("/dev/seed", tags=["dev"])
def seed_database(db: Session = Depends(get_db)) -> dict:
    """
    Development-only seed endpoint.

    Creates one tenant (Harpreet Hosiery Works), 5 SKUs spread across all
    three categories, and 3 Punjab-based vendors — one per category — so
    the entire application is immediately explorable after a fresh install.

    Idempotent: if Harpreet Hosiery Works already exists, returns its
    tenant_id without creating any duplicate rows.

    Each SKU's critical_multiplier is populated from the category default in
    scoring/thresholds.py so thresholds logic is testable right away.

    ⚠️  Never expose this endpoint outside a development environment.
    """
    existing_tenant = (
        db.query(Tenant)
        .filter(Tenant.name == "Harpreet Hosiery Works")
        .first()
    )
    if existing_tenant:
        return {
            "seeded": False,
            "tenant_id": existing_tenant.id,
            "message": "Already seeded. Delete chainflow.db and restart to reseed.",
        }

    # ── Tenant ────────────────────────────────────────────────────────────────
    tenant = Tenant(name="Harpreet Hosiery Works", created_at=datetime.utcnow())
    db.add(tenant)
    db.flush()  # Populate tenant.id so it can be used as an FK below

    # ── SKUs ──────────────────────────────────────────────────────────────────
    for sku_data in _SEED_SKUS:
        sku = SKU(
            tenant_id=tenant.id,
            last_updated=datetime.utcnow(),
            source="manual",
            # Set multiplier from category default — makes thresholds.py
            # immediately testable without any extra configuration.
            critical_multiplier=get_category_default_multiplier(sku_data["category"]),
            **sku_data,
        )
        db.add(sku)

    # ── Vendors ───────────────────────────────────────────────────────────────
    for vendor_data in _SEED_VENDORS:
        vendor = Vendor(
            tenant_id=tenant.id,
            created_at=datetime.utcnow(),
            **vendor_data,
        )
        db.add(vendor)

    db.flush()  # Flush so vendor/SKU IDs are populated before creating links

    # ── VendorSKULinks ────────────────────────────────────────────────────────
    # Link each vendor to the SKUs they supply so the reorder agent's
    # get_vendors_for_sku() plugin returns results.  Without these links the
    # agent finds no vendors and skips every recommendation.
    sharma     = db.query(Vendor).filter(Vendor.name == "Sharma Textiles").first()
    punjab     = db.query(Vendor).filter(Vendor.name == "Punjab Components House").first()
    gupta      = db.query(Vendor).filter(Vendor.name == "Gupta Packaging Works").first()
    nyl_thread = db.query(SKU).filter(SKU.sku_code == "NYL-THREAD-40").first()
    draw_cord  = db.query(SKU).filter(SKU.sku_code == "DRAW-CORD-3MM").first()
    pkg_poly   = db.query(SKU).filter(SKU.sku_code == "PKG-POLY-BAG-M").first()

    if sharma is None or punjab is None or gupta is None:
        db.rollback()
        raise ValueError("Seed data incomplete: one or more vendors not found after flush.")
    if nyl_thread is None or draw_cord is None or pkg_poly is None:
        db.rollback()
        raise ValueError("Seed data incomplete: one or more SKUs not found after flush.")

    links = [
        VendorSKULink(vendor_id=sharma.id,  sku_id=nyl_thread.id, lead_time_days=10, quoted_price=280.0),
        VendorSKULink(vendor_id=punjab.id,  sku_id=draw_cord.id,  lead_time_days=7,  quoted_price=2.2),
        VendorSKULink(vendor_id=gupta.id,   sku_id=pkg_poly.id,   lead_time_days=5,  quoted_price=0.8),
    ]
    for link in links:
        db.add(link)
    db.commit()

    return {
        "seeded": True,
        "tenant_id": tenant.id,
        "skus_created": len(_SEED_SKUS),
        "vendors_created": len(_SEED_VENDORS),
        "links_created": len(links),
        "tip": (
            f"GET /inventory/alerts?tenant_id={tenant.id} → "
            "expect DRAW-CORD-3MM (critical) and NYL-THREAD-40 (low)."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# Demo scenario — rich seed data for Harpreet Hosiery Works
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/dev/demo-scenario", tags=["dev"])
def load_demo_scenario(db: Session = Depends(get_db)) -> dict:
    """
    Wipes and reloads all data for tenant_id=1 with a rich, realistic
    demo dataset for Harpreet Hosiery Works, Ludhiana.

    Safe to call multiple times — always produces the same clean state.

    Creates:
    - 9 SKUs across 3 categories (3 critical, 2 low, 3 ok)
    - 3 vendors with differentiated scores and histories
    - VendorSKULinks linking vendors to the SKUs they supply
    - 30 days of InventoryLog per SKU (for trend charts)
    - 25 DeliveryRecords per vendor spread over 6 months
    - 6 months of SpendRecords (Jan–Jun 2025)
    - 7 ReorderRecommendations (3 approved, 2 rejected, 2 pending)
    - 1 SKU with no vendor links (ELAS-YARN-6MM) for the no-vendor demo path
    - 1 pending SupplierApplication for Kapoor Elastic Supplies
    """
    import random
    from datetime import datetime, timedelta

    TENANT_ID = 1

    # ── 1. WIPE existing data (reverse FK order) ──────────────────────────
    from backend.models import (
        ReorderRecommendation, InventoryLog, VendorSKULink as VendorSKULinkModel,
        SpendRecord, DeliveryRecord, SKU as SKUModel, Vendor as VendorModel,
        SupplierApplication, QuoteRecord, SpendPolicy,
    )

    # quote_records references reorder_recommendations — must go first
    db.query(QuoteRecord).filter(
        QuoteRecord.recommendation_id.in_(
            db.query(ReorderRecommendation.id).filter_by(tenant_id=TENANT_ID)
        )
    ).delete(synchronize_session=False)
    db.query(ReorderRecommendation).filter_by(tenant_id=TENANT_ID).delete()
    db.query(SpendRecord).filter_by(tenant_id=TENANT_ID).delete()
    db.query(DeliveryRecord).filter_by(tenant_id=TENANT_ID).delete()
    db.query(InventoryLog).filter(
        InventoryLog.sku_id.in_(
            db.query(SKUModel.id).filter_by(tenant_id=TENANT_ID)
        )
    ).delete(synchronize_session=False)
    db.query(VendorSKULinkModel).filter(
        VendorSKULinkModel.vendor_id.in_(
            db.query(VendorModel.id).filter_by(tenant_id=TENANT_ID)
        )
    ).delete(synchronize_session=False)
    db.query(SupplierApplication).filter_by(tenant_id=TENANT_ID).delete()
    db.query(SKUModel).filter_by(tenant_id=TENANT_ID).delete()
    db.query(VendorModel).filter_by(tenant_id=TENANT_ID).delete()
    db.query(SpendPolicy).filter_by(tenant_id=TENANT_ID).delete()
    db.commit()

    # ── 2. CREATE SKUs ────────────────────────────────────────────────────
    now = datetime.utcnow()

    sku_data = [
        # (sku_code, name, category, unit, qty, threshold, reorder, cost)
        ("DRAW-CORD-3MM",   "Draw Cord 3mm",           "Raw Material", "metres",  60,   300,  1500,  2.8),
        ("NYL-THREAD-40",   "Nylon Thread 40D",         "Raw Material", "spools",  180,  400,  800,   45.0),
        ("ELASTIC-25MM",    "Elastic Tape 25mm",        "Raw Material", "metres",  850,  600,  2000,  1.2),
        ("COTTON-YARN-2PLY","Cotton Yarn 2-Ply",        "Raw Material", "kg",      120,  500,  1000,  180.0),
        ("POLY-BAG-12X16",  "Poly Bag 12x16 inch",      "Packaging",    "pieces",  2200, 1000, 5000,  0.8),
        ("CARDBOX-MED",     "Medium Cardboard Box",     "Packaging",    "pieces",  380,  500,  1000,  12.0),
        ("EYELET-4MM",      "Brass Eyelet 4mm",         "Components",   "pieces",  8500, 5000, 20000, 0.15),
        ("ZIPPER-20CM",     "Nylon Zipper 20cm",        "Components",   "pieces",  290,  800,  2000,  8.5),
        # 9th SKU — no vendor links, triggers no-vendor exception path
        ("ELAS-YARN-6MM",   "Elastic Yarn 6mm Gauge",   "Raw Material", "metres",  45,   500,  2000,  3.2),
    ]

    skus = {}
    for code, name, cat, unit, qty, threshold, reorder, cost in sku_data:
        sku = SKUModel(
            tenant_id=TENANT_ID,
            sku_code=code,
            name=name,
            category=cat,
            unit=unit,
            current_quantity=float(qty),
            reorder_threshold=float(threshold),
            reorder_quantity=float(reorder),
            unit_cost=float(cost),
            critical_multiplier=get_category_default_multiplier(cat),
            last_updated=now,
            source="tally",
        )
        db.add(sku)
        skus[code] = sku

    db.flush()  # get IDs

    # ── 3. CREATE VENDORS ─────────────────────────────────────────────────
    vendor_data = [
        # (name, city, contact, phone, materials, score, total, on_time, quality, lead)
        (
            "Punjab Components House", "Ludhiana", "Meena Kaur",
            "+91-161-2345678", "Raw Material,Components",
            91.0, 48, 44, 1, 7,
        ),
        (
            "Sharma Textiles", "Panipat", "Vikram Sharma",
            "+91-180-3456789", "Raw Material",
            67.0, 31, 23, 4, 10,
        ),
        (
            "Gupta Packaging Co", "Delhi", "Anil Gupta",
            "+91-11-4567890", "Packaging,Components",
            43.0, 22, 13, 7, 14,
        ),
    ]

    vendors = {}
    for name, city, contact, phone, materials, score, total, on_time, quality, lead in vendor_data:
        v = VendorModel(
            tenant_id=TENANT_ID,
            name=name,
            contact_name=contact,
            phone=phone,
            city=city,
            materials_supplied=materials,
            score=score,
            total_orders=total,
            on_time_deliveries=on_time,
            quality_issues=quality,
            lead_time_days=lead,
            created_at=now - timedelta(days=180),
        )
        db.add(v)
        vendors[name] = v

    db.flush()

    # ── 4. VENDOR SKU LINKS ───────────────────────────────────────────────
    punjab = vendors["Punjab Components House"]
    sharma = vendors["Sharma Textiles"]
    gupta  = vendors["Gupta Packaging Co"]

    links = [
        # Punjab: supplies 5 SKUs
        VendorSKULinkModel(vendor_id=punjab.id, sku_id=skus["DRAW-CORD-3MM"].id,    quoted_price=2.8,   lead_time_days=7),
        VendorSKULinkModel(vendor_id=punjab.id, sku_id=skus["NYL-THREAD-40"].id,    quoted_price=44.0,  lead_time_days=7),
        VendorSKULinkModel(vendor_id=punjab.id, sku_id=skus["EYELET-4MM"].id,       quoted_price=0.14,  lead_time_days=7),
        VendorSKULinkModel(vendor_id=punjab.id, sku_id=skus["ZIPPER-20CM"].id,      quoted_price=8.2,   lead_time_days=7),
        VendorSKULinkModel(vendor_id=punjab.id, sku_id=skus["COTTON-YARN-2PLY"].id, quoted_price=175.0, lead_time_days=7),
        # Sharma: supplies 4 SKUs (overlaps with Punjab on 3)
        VendorSKULinkModel(vendor_id=sharma.id, sku_id=skus["DRAW-CORD-3MM"].id,    quoted_price=3.1,   lead_time_days=10),
        VendorSKULinkModel(vendor_id=sharma.id, sku_id=skus["NYL-THREAD-40"].id,    quoted_price=47.0,  lead_time_days=10),
        VendorSKULinkModel(vendor_id=sharma.id, sku_id=skus["ELASTIC-25MM"].id,     quoted_price=1.3,   lead_time_days=10),
        VendorSKULinkModel(vendor_id=sharma.id, sku_id=skus["COTTON-YARN-2PLY"].id, quoted_price=185.0, lead_time_days=10),
        # Gupta: supplies packaging + some components
        VendorSKULinkModel(vendor_id=gupta.id,  sku_id=skus["POLY-BAG-12X16"].id,   quoted_price=0.9,   lead_time_days=14),
        VendorSKULinkModel(vendor_id=gupta.id,  sku_id=skus["CARDBOX-MED"].id,      quoted_price=13.5,  lead_time_days=14),
        VendorSKULinkModel(vendor_id=gupta.id,  sku_id=skus["EYELET-4MM"].id,       quoted_price=0.16,  lead_time_days=14),
        VendorSKULinkModel(vendor_id=gupta.id,  sku_id=skus["ZIPPER-20CM"].id,      quoted_price=9.0,   lead_time_days=14),
        # ELAS-YARN-6MM intentionally has NO links → triggers no-vendor path
    ]
    for link in links:
        db.add(link)

    # ── 5. INVENTORY LOGS (30 days per SKU) ──────────────────────────────
    log_count = 0
    for sku in skus.values():
        start_qty = sku.current_quantity * 4.0
        daily_drop = (start_qty - sku.current_quantity) / 30.0

        for day in range(30):
            prev = start_qty - daily_drop * day
            new  = start_qty - daily_drop * (day + 1)
            variance = random.uniform(-0.05, 0.05) * daily_drop
            new = max(0.0, new + variance)

            log = InventoryLog(
                sku_id=sku.id,
                previous_quantity=round(prev, 2),
                new_quantity=round(new, 2),
                change_source="tally_sync",
                changed_at=now - timedelta(days=29 - day),
                notes=None,
            )
            db.add(log)
            log_count += 1

    # ── 6. DELIVERY RECORDS (25 per vendor, 6 months) ────────────────────
    delivery_count = 0

    def make_deliveries(vendor, total_count, base_on_time_rate,
                        recent_on_time_rate, quality_issue_indices):
        for i in range(total_count):
            days_ago = int((total_count - i) * (180 / total_count))
            delivered_at = now - timedelta(days=days_ago)
            is_recent = days_ago <= 60
            rate = recent_on_time_rate if is_recent else base_on_time_rate
            was_on_time = random.random() < rate
            had_quality_issue = i in quality_issue_indices
            dr = DeliveryRecord(
                tenant_id=TENANT_ID,
                vendor_id=vendor.id,
                sku_id=None,
                was_on_time=was_on_time,
                had_quality_issue=had_quality_issue,
                delivered_at=delivered_at,
            )
            db.add(dr)
        return total_count

    delivery_count += make_deliveries(
        punjab, 25,
        base_on_time_rate=0.95, recent_on_time_rate=0.96,
        quality_issue_indices={11},
    )
    delivery_count += make_deliveries(
        sharma, 25,
        base_on_time_rate=0.82, recent_on_time_rate=0.60,
        quality_issue_indices={3, 14, 19, 22},
    )
    delivery_count += make_deliveries(
        gupta, 25,
        base_on_time_rate=0.65, recent_on_time_rate=0.50,
        quality_issue_indices={2, 8, 13, 17, 20, 22, 24},
    )

    # ── 7. SPEND RECORDS (Jan–Jun 2025) ──────────────────────────────────
    spend_count = 0

    spend_template = [
        # (vendor, sku_code, months_active, qty_range, price)
        (punjab, "DRAW-CORD-3MM",    [1,2,3,4,5,6], (1000, 1800), 2.8),
        (punjab, "NYL-THREAD-40",    [1,2,3,4,5,6], (300,  700),  44.0),
        (punjab, "COTTON-YARN-2PLY", [2,3,4,5,6],   (200,  500),  175.0),
        (sharma, "DRAW-CORD-3MM",    [1,2,4,5],     (400,  800),  3.1),
        (sharma, "ELASTIC-25MM",     [1,3,5,6],     (800,  1500), 1.3),
        (gupta,  "POLY-BAG-12X16",   [1,2,3,4,5,6], (2000, 4000), 0.9),
        (gupta,  "CARDBOX-MED",      [2,4,6],       (300,  600),  13.5),
    ]

    for vendor, sku_code, months, qty_range, price in spend_template:
        for month in months:
            qty = random.randint(*qty_range)
            sr = SpendRecord(
                tenant_id=TENANT_ID,
                vendor_id=vendor.id,
                sku_id=skus[sku_code].id,
                quantity=float(qty),
                unit_price=price,
                total_value=round(qty * price, 2),
                month=month,
                year=2025,
                created_at=datetime(2025, month, random.randint(5, 25)),
            )
            db.add(sr)
            spend_count += 1

    # ── 8. RECOMMENDATION HISTORY ─────────────────────────────────────────
    rec_history = [
        # (sku_code, vendor, qty, status, days_ago, reasoning)
        ("DRAW-CORD-3MM",    punjab, 1500, "approved", 28,
         "DRAW-CORD-3MM is at 12% of threshold. Punjab Components House scores 91/100 "
         "with 7-day lead time — sufficient to avoid stockout at current consumption rate."),
        ("NYL-THREAD-40",    punjab,  800, "approved", 21,
         "NYL-THREAD-40 at 45% of threshold and declining. Punjab scores 91 vs Sharma's 67. "
         "Recommending Punjab for reliability."),
        ("ZIPPER-20CM",      punjab, 2000, "approved", 14,
         "ZIPPER-20CM critical at 36% of threshold. Only Punjab and Gupta supply this SKU. "
         "Punjab score 91 vs Gupta 43 — Punjab is the clear choice."),
        ("ELASTIC-25MM",     sharma, 2000, "rejected", 21,
         "ELASTIC-25MM below threshold. Sharma Textiles is the only vendor. "
         "Recommend reordering 2000 metres."),
        ("POLY-BAG-12X16",   gupta,  5000, "rejected", 21,
         "POLY-BAG-12X16 at 220% of threshold — actually OK. Raised as precaution "
         "given Gupta's recent quality issues. Rohan rejected: stock level sufficient."),
        ("COTTON-YARN-2PLY", punjab, 1000, "pending",   0,
         "COTTON-YARN-2PLY at 24% of threshold — critical. Current consumption rate "
         "gives 6 days of stock. Punjab lead time is 7 days — order immediately."),
        ("CARDBOX-MED",      gupta,  1000, "pending",   0,
         "CARDBOX-MED at 76% of threshold and declining. Gupta Packaging Co is sole "
         "supplier. Despite low score (43), no alternative exists — recommend reorder."),
    ]

    for sku_code, vendor, qty, status, days_ago, reasoning in rec_history:
        rec = ReorderRecommendation(
            tenant_id=TENANT_ID,
            sku_id=skus[sku_code].id,
            vendor_id=vendor.id,
            quantity=float(qty),
            reasoning=reasoning,
            status=status,
            created_at=now - timedelta(days=days_ago),
            approved_at=(now - timedelta(days=days_ago - 1)) if status == "approved" else None,
            sms_sent=False,
        )
        db.add(rec)

    # ── 9. SUPPLIER APPLICATION (for no-vendor demo path) ─────────────────
    kapoor = SupplierApplication(
        tenant_id=TENANT_ID,
        business_name="Kapoor Elastic Supplies",
        contact_name="Sunita Kapoor",
        contact_email="kapoor.elastic@gmail.com",
        city="Ludhiana",
        gst_number="03AABCK1234D1Z5",
        materials_supplied="Raw Material",
        avg_lead_time_days=8,
        min_order_value_inr=5000.0,
        status="pending",
        applied_at=now - timedelta(days=2),
    )
    db.add(kapoor)

    # ── 10. SPEND POLICY (demo thresholds for tenant) ─────────────────────
    db.add(SpendPolicy(
        tenant_id=TENANT_ID,
        auto_approve_limit=25000.00,
        rohan_limit=100000.00,
        updated_at=now,
    ))

    db.commit()

    return {
        "status": "demo_scenario_loaded",
        "skus": len(skus),
        "vendors": 3,
        "logs": log_count,
        "deliveries": delivery_count,
        "spend_records": spend_count,
        "recommendations": len(rec_history),
        "supplier_applications": 1,
        "note": "ELAS-YARN-6MM has no vendor links — triggers no-vendor exception path in watchdog",
    }
