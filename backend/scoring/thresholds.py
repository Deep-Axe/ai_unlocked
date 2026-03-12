"""
ChainFlow — scoring/thresholds.py
Rule-based stock status computation for the inventory alerting loop.

Module exports
──────────────
    CATEGORY_MULTIPLIERS                  dict[str, float]
        Category → critical threshold multiplier.  Exposed as a module-level
        constant so the dashboard can display the active multiplier per category
        without calling a function.

    get_category_default_multiplier(category: str) -> float
        Called by routers at SKU creation time to pre-populate
        SKU.critical_multiplier from the category default.

    compute_stock_status(sku, vendor_links) -> "critical" | "low" | "ok"
        Primary entry point.  Called by every router endpoint that returns
        an SKUResponse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Avoid circular imports at runtime.
    # thresholds.py has zero runtime imports from the backend package —
    # it only needs the type shapes, which TYPE_CHECKING provides.
    from backend.models import SKU, VendorSKULink


# ──────────────────────────────────────────────────────────────────────────────
# Category multipliers
# ──────────────────────────────────────────────────────────────────────────────

CATEGORY_MULTIPLIERS: dict[str, float] = {
    "Raw Material": 0.35,
    "Components":   0.25,
    "Packaging":    0.15,
}
"""
Fraction of reorder_threshold below which a SKU is considered "critical".

Rationale for the defaults:
    Raw Material (0.35) — production halts immediately when raw inputs run out;
                          larger safety buffer is non-negotiable.
    Components   (0.25) — sub-assembly parts; limited substitution possible
                          for short periods without halting the line.
    Packaging    (0.15) — finished goods can be held briefly if packaging is low;
                          smallest acceptable buffer.

These defaults are calibrated for a hosiery unit (Harpreet Hosiery Works
context).  Enterprise-tier tenants will be able to configure per-tenant weight
overrides in a future sprint once operational baselines are established.
"""

_DEFAULT_MULTIPLIER: float = 0.20
"""Fallback multiplier for unrecognised or unconfigured categories."""


# ──────────────────────────────────────────────────────────────────────────────
# Public helpers
# ──────────────────────────────────────────────────────────────────────────────

def get_category_default_multiplier(category: str) -> float:
    """
    Return the default critical_multiplier for a given SKU category.

    Called by the router (POST /inventory/skus and POST /dev/seed) when
    creating a new SKU, to pre-populate SKU.critical_multiplier in the DB
    with the category-appropriate default.

    Passing an unrecognised category returns the conservative baseline (0.20).
    The stored DB value can be overridden per-SKU via PUT /inventory/skus/{id}
    without changing the category, enabling fine-grained tuning once operational
    data justifies a different threshold.
    """
    return CATEGORY_MULTIPLIERS.get(category, _DEFAULT_MULTIPLIER)


def compute_stock_status(
    sku: "SKU",
    vendor_links: "list[VendorSKULink]",
) -> str:
    """
    Compute the real-time stock status for a SKU.

    Returns one of:
        "critical" — stock is dangerously low; escalate to Harpreet immediately
        "low"      — stock is below reorder threshold; Rohan should act now
        "ok"       — stock is healthy; no action needed

    Algorithm
    ─────────
    1. If reorder_threshold <= 0, return "ok" — threshold not yet configured
       (common for Tally-synced SKUs before Rohan fills them in).

    2. Determine lead_time_factor from the shortest vendor lead time across
       all VendorSKULink rows supplied in vendor_links:
           > 14 days  → 1.3   long supply chain; start reordering earlier
           7–14 days  → 1.0   standard
           < 7 days   → 0.7   fast-turnaround supplier; more tolerance
           no data    → 1.0   no linked vendors or all lead times are null

    3. critical_threshold = reorder_threshold
                            × sku.critical_multiplier
                            × lead_time_factor

    4. Return "critical" if current_quantity < critical_threshold
       Return "low"      if current_quantity < reorder_threshold
       Return "ok"       otherwise

    Why sku.critical_multiplier and not CATEGORY_MULTIPLIERS[sku.category]?
    ────────────────────────────────────────────────────────────────────────
    sku.critical_multiplier is stored in the DB (populated at creation from
    the category default via get_category_default_multiplier()).  Storing it
    allows per-SKU customisation — a high-value SKU in the "Components" category
    can be given a 0.40 multiplier without affecting every other component.
    The category lookup is only used at creation time to set the initial value.

    Passing vendor_links
    ────────────────────
    Pass sku.vendor_links when calling from a context where the SQLAlchemy
    relationship is already loaded (e.g., after a standard ORM query).
    Pass an empty list [] if vendor data is not available — the function will
    fall back to lead_time_factor=1.0 rather than failing.
    """
    if sku.reorder_threshold <= 0:
        return "ok"

    # ── Lead time factor ──────────────────────────────────────────────────────
    lead_times: list[int] = [
        vl.lead_time_days
        for vl in vendor_links
        if vl.lead_time_days is not None
    ]
    if not lead_times:
        lead_time_factor = 1.0
    else:
        min_lead = min(lead_times)
        if min_lead > 14:
            lead_time_factor = 1.3
        elif min_lead >= 7:
            lead_time_factor = 1.0
        else:
            lead_time_factor = 0.7

    # ── Critical threshold ────────────────────────────────────────────────────
    critical_threshold = (
        sku.reorder_threshold * sku.critical_multiplier * lead_time_factor
    )

    if sku.current_quantity < critical_threshold:
        return "critical"
    if sku.current_quantity < sku.reorder_threshold:
        return "low"
    return "ok"
