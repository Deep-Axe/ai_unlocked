"""
ChainFlow — models.py
SQLAlchemy ORM models for all Week 1-2 entities.

Model hierarchy (all scoped under a Tenant):

    Tenant
     ├── SKU  (inventory items, one per sku_code per tenant)
     │    ├── VendorSKULink  (many-to-many bridge to Vendor)
     │    └── InventoryLog   (immutable audit trail)
     └── Vendor
          └── VendorSKULink

Multi-tenancy is enforced at the application layer (tenant_id FK on every
entity) — database-level Row-Level Security is deferred to the Azure SQL
migration in a later sprint.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


# ──────────────────────────────────────────────────────────────────────────────
# Tenant
# ──────────────────────────────────────────────────────────────────────────────

class Tenant(Base):
    """
    Top-level multi-tenancy boundary.

    Every SKU, Vendor, and related record belongs to exactly one Tenant.
    In Week 1-2 the app creates a single tenant via POST /dev/seed.
    Auth (JWT carrying tenant_id) arrives in Week 3.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # ── Relationships ────────────────────────────────────────────────────────
    skus = relationship(
        "SKU",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="select",
    )
    vendors = relationship(
        "Vendor",
        back_populates="tenant",
        cascade="all, delete-orphan",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} name={self.name!r}>"


# ──────────────────────────────────────────────────────────────────────────────
# SKU (inventory item)
# ──────────────────────────────────────────────────────────────────────────────

class SKU(Base):
    """
    A single Stock-Keeping Unit — one physical inventory item type.

    sku_code is unique *within* a tenant.  The same physical product at two
    different tenants gets two separate SKU rows — their data never mingles.

    The reorder_threshold / reorder_quantity fields are the core of the
    Week 3 alerting loop: when current_quantity drops below reorder_threshold
    the Inventory Watchdog fires and surfaces a recommendation to Rohan.

    source tracks where the last quantity update came from:
        "tally"   — synced from Tally ERP via tally_listener.py
        "excel"   — uploaded via POST /inventory/upload/excel
        "manual"  — set directly through the API
    """

    __tablename__ = "skus"
    __table_args__ = (
        # sku_code must be unique per tenant — "NYL-FIT-12MM" is a
        # meaningful internal code, not a global identifier.
        UniqueConstraint("tenant_id", "sku_code", name="uq_tenant_sku_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id"), nullable=False, index=True
    )
    sku_code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)   # "Raw Material" | "Packaging" | "Components"
    unit: Mapped[str] = mapped_column(String(50), nullable=False)        # "kg" | "units" | "metres"
    current_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reorder_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reorder_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unit_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Fraction of reorder_threshold that triggers "critical" status.
    # Pre-populated at SKU creation from scoring.thresholds.get_category_default_multiplier().
    # Stored in the DB so it can be tuned per-SKU without changing the category.
    critical_multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=0.20)
    last_updated: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,   # auto-bumped on every UPDATE
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False, default="manual")
    reorder_pending: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)

    # ── Relationships ────────────────────────────────────────────────────────
    tenant = relationship("Tenant", back_populates="skus")
    vendor_links = relationship(
        "VendorSKULink",
        back_populates="sku",
        cascade="all, delete-orphan",
    )
    inventory_logs = relationship(
        "InventoryLog",
        back_populates="sku",
        cascade="all, delete-orphan",
        order_by="InventoryLog.changed_at.desc()",  # most recent first
    )

    def __repr__(self) -> str:
        return (
            f"<SKU id={self.id} code={self.sku_code!r} "
            f"qty={self.current_quantity} unit={self.unit!r}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# Vendor
# ──────────────────────────────────────────────────────────────────────────────

class Vendor(Base):
    """
    A supplier who can fulfil one or more SKU categories.

    Vendors start with a neutral score of 50.0 (0–100 scale).
    Score is updated by the vendor scoring algorithm in Week 3.
    In Week 1-2 it is set manually via PUT /vendors/{id} if needed.

    on_time_deliveries / total_orders are the raw counters that feed
    the Week 3 scoring algorithm — they accumulate throughout the system's
    lifetime and must never be reset.

    materials_supplied is a comma-separated list of SKU categories this
    vendor can supply, e.g. "Raw Material,Components".  It drives the
    initial vendor-to-SKU matching suggestions.
    """

    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    materials_supplied: Mapped[str] = mapped_column(String(500), nullable=False)  # comma-separated SKU categories
    score: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)          # neutral baseline; 0-100
    total_orders: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    on_time_deliveries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_issues: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    lead_time_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # ── Relationships ────────────────────────────────────────────────────────
    tenant = relationship("Tenant", back_populates="vendors")
    sku_links = relationship(
        "VendorSKULink",
        back_populates="vendor",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Vendor id={self.id} name={self.name!r} "
            f"score={self.score} city={self.city!r}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# VendorSKULink  (many-to-many bridge)
# ──────────────────────────────────────────────────────────────────────────────

class VendorSKULink(Base):
    """
    Records which vendor can supply which SKU, and at what price/lead time.

    This is the many-to-many bridge between Vendor and SKU.
    A vendor can supply many SKUs; a SKU can be sourced from many vendors.

    quoted_price and lead_time_days are nullable because a vendor may be
    linked to a SKU before a formal quote has been received.  These fields
    are updated once a quote arrives via PUT /vendors/{id} or dedicated
    quote endpoints (Week 3).
    """

    __tablename__ = "vendor_sku_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vendor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vendors.id"), nullable=False, index=True
    )
    sku_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("skus.id"), nullable=False, index=True
    )
    quoted_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    lead_time_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Relationships ────────────────────────────────────────────────────────
    vendor = relationship("Vendor", back_populates="sku_links")
    sku = relationship("SKU", back_populates="vendor_links")

    def __repr__(self) -> str:
        return (
            f"<VendorSKULink vendor_id={self.vendor_id} sku_id={self.sku_id} "
            f"price={self.quoted_price} lead={self.lead_time_days}d>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# ReorderRecommendation
# ──────────────────────────────────────────────────────────────────────────────

class ReorderRecommendation(Base):
    """
    An AI-generated reorder recommendation produced by the Semantic Kernel agent.

    Lifecycle: pending → approved / rejected

    When status transitions to "approved" the router sets approved_at and
    optionally fires an SMS via Azure Communication Services (Week 3).
    sms_sent tracks whether the approval notification was successfully
    dispatched so the UI can warn Rohan if it failed.

    reasoning is a free-text field populated by the LLM explaining why this
    SKU needs restocking and why this vendor was selected.  It is stored
    verbatim so Harpreet can audit the AI's logic.
    """

    __tablename__ = "reorder_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    sku_id: Mapped[int] = mapped_column(Integer, ForeignKey("skus.id"), nullable=False, index=True)
    vendor_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=True, index=True)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    # status values: "pending" | "approved" | "rejected"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sms_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # PO system fields — populated as the order progresses
    po_blob_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    invoice_blob_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    po_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    invoice_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    winning_vendor_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("vendors.id"), nullable=True
    )
    order_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    vendors_contacted: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    quotes_received: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ai_quote_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ReorderRecommendation id={self.id} sku_id={self.sku_id} "
            f"vendor_id={self.vendor_id} qty={self.quantity} status={self.status!r}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# InventoryLog  (immutable audit trail)
# ──────────────────────────────────────────────────────────────────────────────

class InventoryLog(Base):
    """
    Immutable audit trail of every quantity change for a SKU.

    A new row is written by:
        - PATCH /inventory/skus/{id}/quantity  → change_source="manual_adjustment"
        - POST  /inventory/sync/tally          → change_source="tally_sync"
        - POST  /inventory/upload/excel        → change_source="excel_upload"

    Rows are never updated or deleted — this is a append-only log.
    It provides the full reconciliation history that Rohan and Harpreet
    need when investigating a discrepancy.
    """

    __tablename__ = "inventory_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    sku_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("skus.id"), nullable=False, index=True
    )
    previous_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    new_quantity: Mapped[float] = mapped_column(Float, nullable=False)
    change_source: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "tally_sync" | "excel_upload" | "manual_adjustment"
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    notes: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    # ── Relationships ────────────────────────────────────────────────────────
    sku = relationship("SKU", back_populates="inventory_logs")

    def __repr__(self) -> str:
        delta = (self.new_quantity or 0) - (self.previous_quantity or 0)
        sign = "+" if delta >= 0 else ""
        return (
            f"<InventoryLog sku_id={self.sku_id} "
            f"{self.previous_quantity}→{self.new_quantity} "
            f"({sign}{delta:.2f}) via {self.change_source!r}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# DeliveryRecord
# ──────────────────────────────────────────────────────────────────────────────

class DeliveryRecord(Base):
    """
    One row per delivery completed by a vendor.
    Used to populate vendor performance history charts and
    to recompute vendor scores from actual transaction data.
    """
    __tablename__ = "delivery_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False)
    vendor_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=False)
    sku_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("skus.id"), nullable=True)
    was_on_time: Mapped[bool] = mapped_column(Boolean, nullable=False)
    had_quality_issue: Mapped[bool] = mapped_column(Boolean, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<DeliveryRecord id={self.id} vendor_id={self.vendor_id} "
            f"on_time={self.was_on_time} quality_issue={self.had_quality_issue}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# SpendRecord
# ──────────────────────────────────────────────────────────────────────────────

class SpendRecord(Base):
    """
    One row per purchase made from a vendor in a given month.
    Used for spend analysis charts in Harpreet's dashboard.
    """
    __tablename__ = "spend_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False)
    vendor_id: Mapped[int] = mapped_column(Integer, ForeignKey("vendors.id"), nullable=False)
    sku_id: Mapped[int] = mapped_column(Integer, ForeignKey("skus.id"), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False)
    total_value: Mapped[float] = mapped_column(Float, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)   # 1–12
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def __repr__(self) -> str:
        return (
            f"<SpendRecord id={self.id} vendor_id={self.vendor_id} "
            f"{self.year}-{self.month:02d} total={self.total_value}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# QuoteRecord
# ──────────────────────────────────────────────────────────────────────────────

class QuoteRecord(Base):
    """
    One row per vendor quote received in response to an RFQ.
    Multiple quotes exist per recommendation (one per vendor contacted).
    """
    __tablename__ = "quote_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recommendation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reorder_recommendations.id"), nullable=False
    )
    vendor_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("vendors.id"), nullable=False
    )
    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id"), nullable=False
    )
    quoted_price: Mapped[float] = mapped_column(Float, nullable=False)
    lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    proforma_blob_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<QuoteRecord id={self.id} rec_id={self.recommendation_id} "
            f"vendor_id={self.vendor_id} price={self.quoted_price}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# SupplierApplication
# ──────────────────────────────────────────────────────────────────────────────

class SupplierApplication(Base):
    """
    A prospective vendor who has applied to become a supplier.
    Used for the no-vendor demo path — when a SKU has no VendorSKULinks,
    the watchdog agent surfaces pending applications for that material category.
    """
    __tablename__ = "supplier_applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False)
    business_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_name: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_email: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    gst_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    materials_supplied: Mapped[str] = mapped_column(String(500), nullable=False)
    avg_lead_time_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    min_order_value_inr: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SupplierApplication id={self.id} "
            f"business={self.business_name!r} status={self.status!r}>"
        )


# ──────────────────────────────────────────────────────────────────────────────
# SpendPolicy
# ──────────────────────────────────────────────────────────────────────────────

class SpendPolicy(Base):
    """
    Per-tenant spend approval thresholds.
    One row per tenant — upserted on first use.
    Thresholds in Indian Rupees.
    """
    __tablename__ = "spend_policies"

    id                 = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id          = mapped_column(Integer, ForeignKey("tenants.id"), nullable=False, unique=True)
    auto_approve_limit = mapped_column(Numeric(12, 2), nullable=False, default=25000.00)
    rohan_limit        = mapped_column(Numeric(12, 2), nullable=False, default=100000.00)
    updated_at         = mapped_column(DateTime, nullable=False, default=datetime.utcnow)

    tenant = relationship("Tenant", backref="spend_policy")
