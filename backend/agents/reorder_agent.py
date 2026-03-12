"""
ChainFlow — agents/reorder_agent.py
Agentic reorder engine — data-first approach (no tool calling).

Architecture:
  1. Python fetches low-stock SKUs and ranked vendors directly from Azure SQL
  2. All data is embedded in the prompt as structured JSON context
  3. gpt-oss-120b reasons over the data and returns a JSON array of recommendations
  4. Python parses the response, validates it, and writes to DB

This avoids the need for --enable-auto-tool-choice on the vLLM deployment.
"""

import asyncio
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from openai import OpenAI
from sqlalchemy.orm import Session

from backend.models import ReorderRecommendation, SKU, Vendor, VendorSKULink, SupplierApplication
from backend.integrations.cosmos_logger import (
    log_watchdog_call,
    get_recent_rejection_feedback,
)


# ── Step 1: Fetch data from DB ────────────────────────────────────────────────

def _fetch_low_stock_skus(tenant_id: int, db: Session) -> list[dict]:
    skus = db.query(SKU).filter(SKU.tenant_id == tenant_id).all()
    alerts = []
    for sku in skus:
        if sku.reorder_threshold > 0 and sku.current_quantity <= sku.reorder_threshold:
            alerts.append({
                "sku_code": sku.sku_code,
                "name": sku.name,
                "current_quantity": float(sku.current_quantity),
                "reorder_threshold": float(sku.reorder_threshold),
                "reorder_quantity": float(sku.reorder_quantity),
                "unit": sku.unit,
                "category": sku.category,
            })
    return alerts


def _fetch_vendors_for_sku(sku_code: str, tenant_id: int, db: Session) -> list[dict]:
    sku = db.query(SKU).filter(
        SKU.sku_code == sku_code, SKU.tenant_id == tenant_id
    ).first()
    if not sku:
        return []
    links = db.query(VendorSKULink).filter(VendorSKULink.sku_id == sku.id).all()
    vendors = []
    for link in links:
        vendor = db.query(Vendor).filter(Vendor.id == link.vendor_id).first()
        if vendor:
            vendors.append({
                "name": vendor.name,
                "score": round(float(vendor.score), 1),
                "lead_time_days": link.lead_time_days,
                "city": vendor.city,
            })
    vendors.sort(key=lambda v: v["score"], reverse=True)
    return vendors


# ── Step 2: Write recommendations to DB ──────────────────────────────────────

def _save_recommendation(
    sku_code: str,
    vendor_name: str,
    quantity: float,
    reasoning: str,
    tenant_id: int,
    db: Session,
) -> dict:
    sku = db.query(SKU).filter(
        SKU.sku_code == sku_code, SKU.tenant_id == tenant_id
    ).first()
    vendor = db.query(Vendor).filter(
        Vendor.name == vendor_name, Vendor.tenant_id == tenant_id
    ).first()

    if not sku:
        return {"status": "error", "reason": f"SKU {sku_code} not found"}
    if not vendor:
        return {"status": "error", "reason": f"Vendor {vendor_name!r} not found"}

    existing = db.query(ReorderRecommendation).filter(
        ReorderRecommendation.sku_id == sku.id,
        ReorderRecommendation.tenant_id == tenant_id,
        ReorderRecommendation.status == "pending",
    ).first()
    if existing:
        return {"status": "skipped", "reason": f"Pending recommendation already exists for {sku_code}"}

    rec = ReorderRecommendation(
        tenant_id=tenant_id,
        sku_id=sku.id,
        vendor_id=vendor.id,
        quantity=quantity,
        reasoning=reasoning,
        status="pending",
        sms_sent=False,
    )
    db.add(rec)
    # reorder_pending stays False until Rohan explicitly approves — not on AI creation
    db.commit()
    db.refresh(rec)
    return {
        "status": "created",
        "recommendation_id": rec.id,
        "sku_code": sku_code,
        "vendor_name": vendor_name,
        "quantity": quantity,
    }


# ── Step 3: Run gpt-oss-120b with data context ──────────────────────────────

def _run_agent_sync(tenant_id: int, db: Session) -> tuple[list[dict], list[dict]]:
    """
    Returns (events_for_stream, created_recommendations).
    events_for_stream is a list of (type, text) tuples for SSE.
    """
    events = []

    # Fetch low-stock SKUs
    events.append(("status", "Fetching low-stock SKUs from database..."))
    low_stock = _fetch_low_stock_skus(tenant_id, db)

    if not low_stock:
        events.append(("token", "✅ All stock levels are healthy — no reorders needed.\n"))
        return events, []

    events.append(("token", f"Found {len(low_stock)} low-stock SKU(s): {', '.join(s['sku_code'] for s in low_stock)}\n"))

    # Fetch vendors for each SKU
    events.append(("status", "Fetching vendors for each SKU..."))
    sku_vendor_map = {}
    for sku in low_stock:
        vendors = _fetch_vendors_for_sku(sku["sku_code"], tenant_id, db)
        sku_vendor_map[sku["sku_code"]] = vendors
        events.append(("token", f"  {sku['sku_code']}: {len(vendors)} vendor(s) available\n"))

    # Handle SKUs with no vendors — create no_vendor recs directly, skip from GPT
    no_vendor_skus = [s for s in low_stock if len(sku_vendor_map[s["sku_code"]]) == 0]
    for sku in no_vendor_skus:
        sku_obj = db.query(SKU).filter(
            SKU.sku_code == sku["sku_code"], SKU.tenant_id == tenant_id
        ).first()
        if not sku_obj:
            events.append(("token", f"  {sku['sku_code']}: no_vendor — SKU row not found, skipped\n"))
            continue
        existing = db.query(ReorderRecommendation).filter(
            ReorderRecommendation.sku_id == sku_obj.id,
            ReorderRecommendation.tenant_id == tenant_id,
            ReorderRecommendation.status == "no_vendor",
        ).first()
        if existing:
            events.append(("token", f"  {sku['sku_code']}: no_vendor rec already exists\n"))
            continue
        # Find pending supplier applications matching this SKU's category
        apps = (
            db.query(SupplierApplication)
            .filter(
                SupplierApplication.tenant_id == tenant_id,
                SupplierApplication.status == "pending",
                SupplierApplication.materials_supplied.ilike(f"%{sku['category']}%"),
            )
            .all()
        )
        app_names = ", ".join(a.business_name for a in apps) if apps else "none"
        reasoning = (
            f"No vendors are linked to {sku['sku_code']}. "
            f"Stock is critically low: {int(sku['current_quantity'])}/{int(sku['reorder_threshold'])} {sku['unit']}. "
            f"{len(apps)} supplier application(s) pending: {app_names}."
        )
        rec = ReorderRecommendation(
            tenant_id=tenant_id,
            sku_id=sku_obj.id,
            vendor_id=None,
            quantity=float(sku["reorder_quantity"]),
            reasoning=reasoning,
            status="no_vendor",
            sms_sent=False,
        )
        db.add(rec)
        db.commit()
        events.append(("token", f"⚠ {sku['sku_code']}: no vendors — created no_vendor rec (apps: {app_names})\n"))
    # Remove no-vendor SKUs so they are not sent to GPT
    low_stock = [s for s in low_stock if len(sku_vendor_map[s["sku_code"]]) > 0]
    if not low_stock:
        events.append(("token", "No vendored SKUs remain for GPT analysis.\n"))
        return events, []

    # Build prompt context
    # Fetch rejection feedback for each SKU to inject into the prompt
    rejection_context_parts = []
    for sku in low_stock:
        past_rejections = get_recent_rejection_feedback(
            tenant_id=tenant_id,
            sku_code=sku["sku_code"],
        )
        if past_rejections:
            rejection_context_parts.append(
                f"{sku['sku_code']}: " + "; ".join(past_rejections)
            )

    context_lines = []
    for sku in low_stock:
        vendors = sku_vendor_map[sku["sku_code"]]
        vendor_str = ", ".join(
            f"{v['name']} (score={v['score']}, lead={v['lead_time_days']}d)"
            for v in vendors
        ) or "no vendors linked"
        context_lines.append(
            f"- {sku['sku_code']} ({sku['name']}): stock={sku['current_quantity']}{sku['unit']}, "
            f"threshold={sku['reorder_threshold']}, reorder_qty={sku['reorder_quantity']} | vendors: {vendor_str}"
        )
    context = "\n".join(context_lines)

    events.append(("status", "Asking gpt-oss-120b to reason and generate recommendations..."))

    client = OpenAI(
        base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
    )

    system_prompt = (
        "You are a supply chain AI agent for Harpreet Hosiery Works, an Indian manufacturing MSME. "
        "You will be given a list of low-stock items with their vendors. "
        "Your job is to decide which vendor to use for each reorder and write a brief reasoning. "
        "Always pick the highest-scored vendor unless there is a clear reason not to. "
        "Respond ONLY with a valid JSON array. No explanation outside the JSON. "
        "Each element must have exactly these fields: "
        '"sku_code" (string), "vendor_name" (string, must match exactly), '
        '"quantity" (number, use the reorder_qty), "reasoning" (1-2 sentences).'
    )

    rejection_block = ""
    if rejection_context_parts:
        rejection_block = (
            "\n\nIMPORTANT — Previous recommendations for the following SKUs were rejected:\n"
            + "\n".join(f"- {r}" for r in rejection_context_parts)
            + "\nTake these rejections into account. Only recommend if the situation "
              "has materially changed since the last rejection."
        )

    user_prompt = (
        f"Low-stock inventory that needs reorder recommendations:\n{context}\n\n"
        "Return a JSON array of recommendations for every SKU listed above."
        + rejection_block
    )

    _t0 = time.time()
    response = client.chat.completions.create(
        model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=3000,
        temperature=0.1,
    )
    _latency_ms = int((time.time() - _t0) * 1000)

    raw = (response.choices[0].message.content or "").strip()
    events.append(("token", f"gpt-oss-120b response received ({len(raw)} chars)\n"))

    # Parse JSON — strip markdown fences if present
    json_str = re.sub(r"^```[\w]*\n?", "", raw)
    json_str = re.sub(r"\n?```$", "", json_str).strip()
    # Extract first JSON array if there's surrounding text
    match = re.search(r"\[.*\]", json_str, re.DOTALL)
    if match:
        json_str = match.group(0)

    recommendations = json.loads(json_str)

    # Write each recommendation to DB
    created = []
    events.append(("status", "Saving recommendations to database..."))
    for rec in recommendations:
        qty = rec.get("quantity")
        if qty is None:
            events.append(("token", f"  {rec.get('sku_code','?')}: skipped (model returned no quantity)\n"))
            continue
        result = _save_recommendation(
            sku_code=rec["sku_code"],
            vendor_name=rec["vendor_name"],
            quantity=float(qty),
            reasoning=rec["reasoning"],
            tenant_id=tenant_id,
            db=db,
        )
        if result["status"] == "created":
            created.append(result)
            events.append(("token", f"✓ {rec['sku_code']} → {rec['vendor_name']} ({int(qty)} units)\n"))
            # Log this watchdog call to Cosmos DB
            sku_meta = next((s for s in low_stock if s["sku_code"] == rec["sku_code"]), {})
            log_watchdog_call(
                tenant_id=tenant_id,
                sku_code=rec["sku_code"],
                recommendation_id=result["recommendation_id"],
                input_summary=(
                    f"{rec['sku_code']}: qty={sku_meta.get('current_quantity','?')}/"
                    f"{sku_meta.get('reorder_threshold','?')} {sku_meta.get('unit','')}, "
                    f"vendor={rec['vendor_name']}"
                ),
                reasoning=rec["reasoning"],
                latency_ms=_latency_ms,
                prompt_tokens=getattr(getattr(response, "usage", None), "prompt_tokens", 0),
                completion_tokens=getattr(getattr(response, "usage", None), "completion_tokens", 0),
            )
        else:
            events.append(("token", f"  {rec['sku_code']}: {result['reason']}\n"))

    return events, created


async def run_reorder_agent(tenant_id: int, db: Session) -> list[dict]:
    """Async entry point — wraps the sync agent in a thread executor."""
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor() as pool:
        _, created = await loop.run_in_executor(
            pool, lambda: _run_agent_sync(tenant_id, db)
        )
    return created


