"""
ChainFlow — routers/analytics.py
Analytics endpoints for dashboard charts.

These endpoints are read-only. They aggregate data from DeliveryRecord,
SpendRecord, and InventoryLog tables for use in recharts components.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta

from backend.database import get_db
from backend.models import DeliveryRecord, SpendRecord, InventoryLog, Vendor, SKU

router = APIRouter()


@router.get("/spend")
def get_spend_analytics(tenant_id: int, db: Session = Depends(get_db)):
    """
    Monthly spend per vendor for the last 6 months.
    Returns data shaped for a recharts LineChart with one line per vendor.

    Response shape:
    {
      "monthly": [
        {"month": 1, "year": 2025, "vendor_name": "Punjab Components House",
         "total_value": 67000.0},
        ...
      ]
    }
    """
    records = (
        db.query(
            SpendRecord.month,
            SpendRecord.year,
            Vendor.name.label("vendor_name"),
            func.sum(SpendRecord.total_value).label("total_value"),
        )
        .join(Vendor, Vendor.id == SpendRecord.vendor_id)
        .filter(SpendRecord.tenant_id == tenant_id)
        .group_by(SpendRecord.month, SpendRecord.year, Vendor.name)
        .order_by(SpendRecord.year, SpendRecord.month)
        .all()
    )
    return {
        "monthly": [
            {
                "month": r.month,
                "year": r.year,
                "vendor_name": r.vendor_name,
                "total_value": round(r.total_value, 2),
            }
            for r in records
        ]
    }


@router.get("/vendor-comparison")
def get_vendor_comparison(tenant_id: int, db: Session = Depends(get_db)):
    """
    Per-vendor summary for grouped bar chart in Harpreet's Vendor Health tab.

    Returns score, on_time_rate, total_spend, avg_lead_time for each vendor.
    """
    vendors = db.query(Vendor).filter(Vendor.tenant_id == tenant_id).all()
    result = []

    for v in vendors:
        total_spend = (
            db.query(func.sum(SpendRecord.total_value))
            .filter(
                SpendRecord.vendor_id == v.id,
                SpendRecord.tenant_id == tenant_id,
            )
            .scalar() or 0.0
        )
        on_time_rate = (
            round(v.on_time_deliveries / v.total_orders * 100, 1)
            if v.total_orders > 0
            else 0.0
        )
        result.append({
            "vendor_id": v.id,
            "vendor_name": v.name,
            "score": round(v.score, 1),
            "on_time_rate": on_time_rate,
            "total_orders": v.total_orders,
            "quality_issues": v.quality_issues,
            "total_spend": round(total_spend, 2),
            "avg_lead_time": v.lead_time_days,
            "city": v.city,
        })

    return result
