"""
ChainFlow — routers/agents.py
Exposes the azure-ai-inference tool-calling reorder agent as FastAPI endpoints.

Endpoints:
  POST /agents/run-watchdog                          run reorder agent for a tenant
  GET  /agents/recommendations                       list pending recommendations
  POST /agents/recommendations/{id}/approve          approve a recommendation
  POST /agents/recommendations/{id}/reject           reject a recommendation
  GET  /agents/run-watchdog-stream                   SSE — streams tool-call steps live
"""

from datetime import datetime
import asyncio
import json
import logging
import os
import re
import time
import traceback
import uuid
from typing import Any, Dict

logger = logging.getLogger("chainflow.agent")

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend.agents.reorder_agent import run_reorder_agent
from backend.database import get_db
from backend.models import QuoteRecord, ReorderRecommendation, SKU, SpendPolicy, SupplierApplication, Vendor, VendorSKULink
from backend.integrations.blob_storage import upload_document, get_sas_url
from backend.integrations.po_generator import (
    generate_proforma_pdf, generate_purchase_order_pdf, generate_tax_invoice_pdf
)

router = APIRouter()

# In-memory job store — resets on server restart, fine for demo
_jobs: Dict[str, Dict[str, Any]] = {}


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/run-watchdog
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/run-watchdog")
async def run_watchdog(tenant_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """
    Start the reorder agent as a background job.
    Returns a job_id immediately — client polls GET /agents/job/{job_id}
    every 2s for completion.
    """
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None, "error": None}

    async def _run():
        try:
            recommendations = await run_reorder_agent(tenant_id=tenant_id, db=db)
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = {
                "recommendations_created": len(recommendations),
                "recommendations": recommendations,
            }
        except Exception as exc:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(exc)

    background_tasks.add_task(_run)
    return {"job_id": job_id, "status": "running"}


# ──────────────────────────────────────────────────────────────────────────────
# GET /agents/job/{job_id}
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/job/{job_id}")
def get_job_status(job_id: str):
    """Poll every 2s after calling run-watchdog to check completion."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": job["status"],   # "running" | "done" | "error"
        "result": job["result"],   # populated when done
        "error": job["error"],
    }


# ──────────────────────────────────────────────────────────────────────────────
# GET /agents/run-watchdog-stream  (SSE — Phi-4 tokens stream live)
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/run-watchdog-stream")
async def run_watchdog_stream(tenant_id: int):
    async def event_stream():
        from backend.database import SessionLocal
        db = SessionLocal()
        try:
            import asyncio
            from concurrent.futures import ThreadPoolExecutor
            from backend.agents.reorder_agent import _run_agent_sync

            yield f"data: {json.dumps({'type': 'status', 'message': 'Agent starting — checking inventory...'})}\n\n"

            loop = asyncio.get_running_loop()
            with ThreadPoolExecutor() as pool:
                events, saved = await loop.run_in_executor(
                    pool, lambda: _run_agent_sync(tenant_id, db)
                )

            for event_type, text in events:
                if event_type == "status":
                    yield f"data: {json.dumps({'type': 'status', 'message': text})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'token', 'text': text})}\n\n"

            yield f"data: {json.dumps({'type': 'done', 'recommendations': saved})}\n\n"

        except Exception as exc:
            tb = traceback.format_exc()
            exc_type = type(exc).__name__
            exc_str  = str(exc)

            if "401" in exc_str or "Unauthorized" in exc_str:
                logger.error("\n AUTHENTICATION FAILED\n   Detail: %s", exc_str)
            elif "404" in exc_str:
                logger.error("\n MODEL NOT FOUND (current: %s)\n   Detail: %s",
                             os.environ.get("AZURE_OPENAI_DEPLOYMENT", "<not set>"), exc_str)
            elif "429" in exc_str:
                logger.error("\n RATE LIMIT\n   Detail: %s", exc_str)
            elif "JSONDecodeError" in exc_type or "json" in exc_str.lower():
                logger.error("\nINVALID JSON IN RESPONSE\n%s", tb)
            elif "Timeout" in exc_type or "timeout" in exc_str.lower():
                logger.error("\n  TIMEOUT (read_timeout=90s exceeded)\n   Detail: %s", exc_str)
            else:
                logger.error("\n AGENT ERROR (%s)\n%s", exc_type, tb)

            yield f"data: {json.dumps({'type': 'error', 'message': exc_str})}\n\n"
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )




# ──────────────────────────────────────────────────────────────────────────────
# GET /agents/recommendations
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/recommendations")
def get_recommendations(tenant_id: int, db: Session = Depends(get_db)):
    """
    List all pending reorder recommendations for a tenant, newest first.

    Only returns status="pending" records — approved/rejected recommendations
    are archived and accessible via a future audit endpoint.
    """
    recs = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.tenant_id == tenant_id,
            ReorderRecommendation.status.in_(["pending", "no_vendor"]),
        )
        .order_by(ReorderRecommendation.created_at.desc())
        .all()
    )
    result = []
    for rec in recs:
        sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()
        vendor = db.query(Vendor).filter(Vendor.id == rec.vendor_id).first()

        # All vendors that supply this SKU, for Rohan's vendor selector
        links = (
            db.query(VendorSKULink)
            .filter(VendorSKULink.sku_id == rec.sku_id)
            .all()
        )
        available_vendors = []
        for link in links:
            v = db.query(Vendor).filter(Vendor.id == link.vendor_id).first()
            if v:
                available_vendors.append({
                    "vendor_id": v.id,
                    "vendor_name": v.name,
                    "score": round(float(v.score), 1),
                    "quoted_price": link.quoted_price,
                    "lead_time_days": link.lead_time_days,
                })

        # For no_vendor recs, surface matching pending supplier applications
        supplier_applications = []
        if rec.status == "no_vendor" and sku:
            apps = (
                db.query(SupplierApplication)
                .filter(
                    SupplierApplication.tenant_id == tenant_id,
                    SupplierApplication.status == "pending",
                    SupplierApplication.materials_supplied.ilike(f"%{sku.category}%"),
                )
                .all()
            )
            for app in apps:
                supplier_applications.append({
                    "id": app.id,
                    "business_name": app.business_name,
                    "contact_name": app.contact_name,
                    "contact_email": app.contact_email,
                    "city": app.city,
                    "materials_supplied": app.materials_supplied,
                    "avg_lead_time_days": app.avg_lead_time_days,
                    "min_order_value_inr": float(app.min_order_value_inr) if app.min_order_value_inr else None,
                    "applied_at": app.applied_at.isoformat() if app.applied_at else None,
                })

        result.append({
            "id": rec.id,
            "sku_code": sku.sku_code if sku else None,
            "sku_name": sku.name if sku else None,
            "sku_category": sku.category if sku else None,
            "unit": sku.unit if sku else None,
            "vendor_id": rec.vendor_id,
            "vendor_name": vendor.name if vendor else None,
            "quantity": rec.quantity,
            "reasoning": rec.reasoning,
            "status": rec.status,
            "created_at": rec.created_at.isoformat(),
            "available_vendors": available_vendors,
            "supplier_applications": supplier_applications,
        })
    return result


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/recommendations/{recommendation_id}/approve
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/recommendations/{recommendation_id}/approve")
def approve_recommendation(
    recommendation_id: int,
    tenant_id: int,
    background_tasks: BackgroundTasks,
    vendor_id: int | None = None,
    db: Session = Depends(get_db),
):
    """
    Approve a pending reorder recommendation.

    Sets status=approved instantly and fires the confirmation email
    as a background task — endpoint returns in <50ms.
    """
    from backend.integrations.notification_service import send_approval_email

    rec = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.id == recommendation_id,
            ReorderRecommendation.tenant_id == tenant_id,
        )
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()
    vendor = db.query(Vendor).filter(Vendor.id == rec.vendor_id).first()

    rec.status = "approved"
    rec.approved_at = datetime.utcnow()
    rec.sms_sent = False

    # Allow Rohan to override the AI-suggested vendor
    if vendor_id is not None:
        rec.vendor_id = vendor_id

    # Mark the SKU so the Alerts tab shows "Reorder Placed" immediately
    sku_obj = db.query(SKU).filter(SKU.id == rec.sku_id).first()
    if sku_obj:
        sku_obj.reorder_pending = True

    db.commit()
    # Email is sent by send-rfq (combined approval + RFQ email)
    return {
        "approved": True,
        "recommendation_id": recommendation_id,
        "email_sent": "pending",
        "sms_sent": False,
    }


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/recommendations/{recommendation_id}/reject
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/recommendations/{recommendation_id}/reject")
def reject_recommendation(
    recommendation_id: int,
    tenant_id: int,
    reason: str = "",
    db: Session = Depends(get_db),
):
    """
    Reject a pending reorder recommendation.

    Sets status="rejected".  The record is kept for audit purposes.
    """
    rec = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.id == recommendation_id,
            ReorderRecommendation.tenant_id == tenant_id,
        )
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()
    sku_code = sku.sku_code if sku else f"SKU#{rec.sku_id}"

    rec.status = "rejected"

    # Clear reorder_pending on the SKU if no other pending recs exist for it
    other_pending = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.sku_id == rec.sku_id,
            ReorderRecommendation.tenant_id == tenant_id,
            ReorderRecommendation.id != recommendation_id,
            ReorderRecommendation.status == "pending",
        )
        .first()
    )
    if not other_pending:
        sku_obj = db.query(SKU).filter(SKU.id == rec.sku_id).first()
        if sku_obj:
            sku_obj.reorder_pending = False

    db.commit()

    from backend.integrations.cosmos_logger import write_rejection_feedback
    feedback_text = reason.strip() if reason.strip() else (
        f"Procurement manager rejected reorder of {int(rec.quantity)} units "
        f"(vendor#{rec.vendor_id}). No reason provided."
    )
    write_rejection_feedback(
        tenant_id=tenant_id,
        recommendation_id=recommendation_id,
        sku_code=sku_code,
        reason=feedback_text,
    )

    return {"rejected": True, "recommendation_id": recommendation_id}


# ──────────────────────────────────────────────────────────────────────────────
# GET  /agents/supplier-applications
# POST /agents/supplier-applications/{id}/approve
# POST /agents/supplier-applications/{id}/reject
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/supplier-applications")
def list_supplier_applications(tenant_id: int, db: Session = Depends(get_db)):
    """List all pending supplier applications for a tenant."""
    apps = (
        db.query(SupplierApplication)
        .filter(SupplierApplication.tenant_id == tenant_id)
        .order_by(SupplierApplication.applied_at.desc())
        .all()
    )
    return [
        {
            "id": a.id,
            "business_name": a.business_name,
            "contact_name": a.contact_name,
            "contact_email": a.contact_email,
            "city": a.city,
            "gst_number": a.gst_number,
            "materials_supplied": a.materials_supplied,
            "avg_lead_time_days": a.avg_lead_time_days,
            "min_order_value_inr": float(a.min_order_value_inr) if a.min_order_value_inr else None,
            "status": a.status,
            "applied_at": a.applied_at.isoformat() if a.applied_at else None,
            "notes": a.notes,
        }
        for a in apps
    ]


@router.post("/supplier-applications/{application_id}/approve")
def approve_supplier_application(
    application_id: int,
    tenant_id: int,
    sku_code: str,
    quoted_price: float,
    lead_time_days: int,
    db: Session = Depends(get_db),
):
    """
    Approve a supplier application:
    1. Mark the application as 'approved'.
    2. Create a new Vendor row from the application data.
    3. Create a VendorSKULink for the given sku_code.
    4. Transition any no_vendor rec for that SKU to 'pending' so normal approve→RFQ flow resumes.
    """
    app = (
        db.query(SupplierApplication)
        .filter(
            SupplierApplication.id == application_id,
            SupplierApplication.tenant_id == tenant_id,
        )
        .first()
    )
    if not app:
        raise HTTPException(status_code=404, detail="Supplier application not found")
    if app.status != "pending":
        raise HTTPException(status_code=400, detail=f"Application is already '{app.status}'")

    sku = db.query(SKU).filter(SKU.sku_code == sku_code, SKU.tenant_id == tenant_id).first()
    if not sku:
        raise HTTPException(status_code=404, detail=f"SKU '{sku_code}' not found for this tenant")

    # 1. Mark application approved
    app.status = "approved"

    # 2. Create Vendor
    new_vendor = Vendor(
        tenant_id=tenant_id,
        name=app.business_name,
        contact_name=app.contact_name,
        email=app.contact_email,
        phone=app.contact_email,  # no phone on app; use email as placeholder
        city=app.city,
        materials_supplied=app.materials_supplied,
        score=70.0,  # default onboarding score
        on_time_deliveries=0,
        quality_issues=0,
        total_orders=0,
    )
    db.add(new_vendor)
    db.flush()  # get new_vendor.id

    # 3. Create VendorSKULink
    link = VendorSKULink(
        vendor_id=new_vendor.id,
        sku_id=sku.id,
        quoted_price=quoted_price,
        lead_time_days=lead_time_days,
    )
    db.add(link)

    # 4. Transition no_vendor rec → pending
    no_vendor_rec = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.sku_id == sku.id,
            ReorderRecommendation.tenant_id == tenant_id,
            ReorderRecommendation.status == "no_vendor",
        )
        .first()
    )
    if no_vendor_rec:
        no_vendor_rec.vendor_id = new_vendor.id
        no_vendor_rec.status = "pending"
        no_vendor_rec.reasoning = (
            f"{no_vendor_rec.reasoning} "
            f"Supplier {app.business_name} has now been onboarded."
        )

    db.commit()

    return {
        "approved": True,
        "application_id": application_id,
        "vendor_id": new_vendor.id,
        "vendor_name": new_vendor.name,
        "sku_code": sku_code,
        "rec_transitioned": no_vendor_rec is not None,
    }


@router.post("/supplier-applications/{application_id}/reject")
def reject_supplier_application(
    application_id: int,
    tenant_id: int,
    reason: str = "",
    db: Session = Depends(get_db),
):
    """Reject a supplier application — marks it 'rejected' with an optional reason."""
    app = (
        db.query(SupplierApplication)
        .filter(
            SupplierApplication.id == application_id,
            SupplierApplication.tenant_id == tenant_id,
        )
        .first()
    )
    if not app:
        raise HTTPException(status_code=404, detail="Supplier application not found")
    if app.status != "pending":
        raise HTTPException(status_code=400, detail=f"Application is already '{app.status}'")

    app.status = "rejected"
    if reason.strip():
        app.notes = (app.notes or "") + f"\n[Rejected] {reason.strip()}"
    db.commit()

    return {"rejected": True, "application_id": application_id}


# ──────────────────────────────────────────────────────────────────────────────
# GET /agents/recommendations/history
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/recommendations/history")
def get_recommendation_history(tenant_id: int, db: Session = Depends(get_db)):
    """
    All recommendations across all statuses, newest first.
    Used for the Past Recommendations collapsible section in Rohan's UI
    and for the Orders tab pipeline view.
    """
    recs = (
        db.query(ReorderRecommendation)
        .filter(ReorderRecommendation.tenant_id == tenant_id)
        .order_by(ReorderRecommendation.created_at.desc())
        .all()
    )
    result = []
    for rec in recs:
        sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()
        vendor = db.query(Vendor).filter(Vendor.id == rec.vendor_id).first()
        result.append({
            "id": rec.id,
            "sku_code": sku.sku_code if sku else None,
            "vendor_name": vendor.name if vendor else None,
            "quantity": rec.quantity,
            "reasoning": rec.reasoning,
            "status": rec.status,
            "created_at": rec.created_at.isoformat(),
            "approved_at": rec.approved_at.isoformat() if rec.approved_at else None,
        })
    return result


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/recommendations/{id}/send-rfq
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/recommendations/{recommendation_id}/send-rfq")
def send_rfq(
    recommendation_id: int,
    tenant_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Send RFQ to all vendors linked to this SKU.
    Sets status='rfq_sent', fires RFQ confirmation email,
    and triggers the Vendor Simulator which calls /quote-received per vendor.
    """
    import httpx

    rec = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.id == recommendation_id,
            ReorderRecommendation.tenant_id == tenant_id,
        )
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if rec.status not in ("pending", "approved"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot send RFQ from status '{rec.status}'"
        )

    sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()
    links = (
        db.query(VendorSKULink)
        .filter(VendorSKULink.sku_id == rec.sku_id)
        .all()
    )
    if not links:
        raise HTTPException(
            status_code=400,
            detail=f"No vendors linked to SKU {sku.sku_code if sku else rec.sku_id}"
        )

    # Split vendors: Meena (Punjab Components House) responds via frontend,
    # all others respond via the Vendor Simulator.
    simulator_vendors = []
    meena_vendor = None
    for link in links:
        vendor = db.query(Vendor).filter(Vendor.id == link.vendor_id).first()
        if not vendor:
            continue
        if "Punjab" in vendor.name:
            meena_vendor = vendor          # Meena quotes via portal
        else:
            simulator_vendors.append({
                "id": vendor.id,
                "name": vendor.name,
                "base_price": link.quoted_price or 0,
                "lead_time_days": link.lead_time_days or 7,
            })

    total_vendors = len(simulator_vendors) + (1 if meena_vendor else 0)
    if total_vendors == 0:
        raise HTTPException(status_code=400, detail="No vendors found for this SKU")

    first_price = links[0].quoted_price or 0
    rec.order_value = round(rec.quantity * first_price, 2)
    rec.status = "rfq_sent"
    rec.vendors_contacted = total_vendors
    rec.quotes_received = 0
    db.commit()

    from backend.integrations.notification_service import send_rfq_confirmation_email
    background_tasks.add_task(
        send_rfq_confirmation_email,
        sku_code=sku.sku_code if sku else f"SKU#{rec.sku_id}",
        vendor_count=total_vendors,
        quantity=rec.quantity,
        unit=sku.unit if sku else "units",
        recommendation_id=recommendation_id,
        reasoning=rec.reasoning,
        sku_name=sku.name if sku else None,
        meena_notified=meena_vendor is not None,
    )

    if simulator_vendors:
        simulator_url = os.environ.get("VENDOR_SIMULATOR_URL", "http://localhost:8001")
        try:
            with httpx.Client(timeout=5.0) as client:
                client.post(
                    f"{simulator_url}/rfq-received",
                    json={
                        "rec_id": recommendation_id,
                        "sku_code": sku.sku_code if sku else "",
                        "sku_description": sku.name if sku else "Supply of materials",
                        "quantity": rec.quantity,
                        "unit": sku.unit if sku else "units",
                        "tenant_id": tenant_id,
                        "vendors": simulator_vendors,
                    },
                )
        except Exception as exc:
            logger.warning("Vendor Simulator not reachable: %s", exc)

    return {
        "rfq_sent": True,
        "recommendation_id": recommendation_id,
        "vendors_contacted": total_vendors,
        "meena_vendor_id": meena_vendor.id if meena_vendor else None,
        "order_value": rec.order_value,
        "sku_code": sku.sku_code if sku else None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/recommendations/{id}/meena-quote  (called from Meena's dashboard)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/recommendations/{recommendation_id}/meena-quote")
def submit_meena_quote(
    recommendation_id: int,
    tenant_id: int,
    payload: dict,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Called from Meena's frontend dashboard when she submits her proforma quote.
    Generates proforma PDF, uploads to blob, sends email with download link,
    stores QuoteRecord. If all vendors have now quoted, runs AI comparison.
    """
    rec = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.id == recommendation_id,
            ReorderRecommendation.tenant_id == tenant_id,
        )
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if rec.status != "rfq_sent":
        raise HTTPException(
            status_code=400,
            detail=f"Cannot submit quote from status '{rec.status}'"
        )

    sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()

    # Find Meena's vendor (Punjab Components House)
    meena_vendor = (
        db.query(Vendor)
        .filter(Vendor.name.ilike("%Punjab%"), Vendor.tenant_id == tenant_id)
        .first()
    )
    if not meena_vendor:
        raise HTTPException(status_code=400, detail="Meena's vendor (Punjab) not found")

    # Check Meena hasn't already quoted
    existing = (
        db.query(QuoteRecord)
        .filter(
            QuoteRecord.recommendation_id == recommendation_id,
            QuoteRecord.vendor_id == meena_vendor.id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Meena has already submitted a quote")

    unit_price = float(payload["unit_price"])
    lead_time_days = int(payload["lead_time_days"])

    # Generate proforma PDF and upload to blob
    from backend.integrations.po_generator import generate_proforma_pdf
    from backend.integrations.blob_storage import upload_document, get_sas_url

    pdf_bytes = generate_proforma_pdf(
        rec_id=recommendation_id,
        sku_code=sku.sku_code if sku else "",
        vendor_name=meena_vendor.name,
        quantity=rec.quantity,
        unit_price=unit_price,
        unit=sku.unit if sku else "units",
        lead_time_days=lead_time_days,
        sku_description=sku.name if sku else "Supply of materials",
    )
    blob_path = f"tenant-{tenant_id}/proformas/PFMA-{recommendation_id:04d}-{meena_vendor.id}.pdf"
    upload_document(blob_path, pdf_bytes)
    sas_url = get_sas_url(blob_path)

    # Store QuoteRecord
    quote = QuoteRecord(
        recommendation_id=recommendation_id,
        vendor_id=meena_vendor.id,
        tenant_id=rec.tenant_id,
        quoted_price=unit_price,
        lead_time_days=lead_time_days,
        proforma_blob_path=blob_path,
        received_at=datetime.utcnow(),
    )
    db.add(quote)
    rec.quotes_received = (rec.quotes_received or 0) + 1
    db.flush()

    all_quotes_in = (rec.quotes_received or 0) >= (rec.vendors_contacted or 1)

    winning_vendor_name = None
    if all_quotes_in:
        rec.status = "quotes_received"
        all_quotes = (
            db.query(QuoteRecord)
            .filter(QuoteRecord.recommendation_id == recommendation_id)
            .all()
        )
        quotes_with_vendors = []
        for q in all_quotes:
            v = db.query(Vendor).filter(Vendor.id == q.vendor_id).first()
            quotes_with_vendors.append({
                "vendor_id": q.vendor_id,
                "vendor_name": v.name if v else f"Vendor#{q.vendor_id}",
                "score": round(float(v.score), 1) if v else 0,
                "quoted_price": q.quoted_price,
                "lead_time_days": q.lead_time_days,
            })
        winner_name, reasoning = _ai_compare_quotes(
            sku_code=sku.sku_code if sku else "",
            quantity=rec.quantity,
            unit=sku.unit if sku else "units",
            quotes=quotes_with_vendors,
            tenant_id=rec.tenant_id,
            recommendation_id=recommendation_id,
        )
        winning_vendor_name = winner_name
        for q_info in quotes_with_vendors:
            if q_info["vendor_name"] == winner_name:
                wv = db.query(Vendor).filter(Vendor.id == q_info["vendor_id"]).first()
                if wv:
                    rec.winning_vendor_id = wv.id
                break
        rec.ai_quote_reasoning = reasoning

    db.commit()

    # Send proforma email with real PDF download link
    from backend.integrations.notification_service import send_proforma_email
    background_tasks.add_task(
        send_proforma_email,
        sku_code=sku.sku_code if sku else "",
        vendor_name=meena_vendor.name,
        quantity=rec.quantity,
        unit=sku.unit if sku else "units",
        unit_price=unit_price,
        lead_days=lead_time_days,
        recommendation_id=recommendation_id,
        proforma_sas_url=sas_url,
    )

    return {
        "quote_submitted": True,
        "vendor": meena_vendor.name,
        "proforma_url": sas_url,
        "all_quotes_in": all_quotes_in,
        "winning_vendor": winning_vendor_name,
    }


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/recommendations/{id}/quote-received  (called by Vendor Simulator)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/recommendations/{recommendation_id}/quote-received")
def quote_received(
    recommendation_id: int,
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    Called by the Vendor Simulator when a vendor responds with a quote.
    Stores QuoteRecord. When all vendors have responded, calls GPT-OSS-120B
    to compare quotes and select the winning vendor.
    """
    rec = (
        db.query(ReorderRecommendation)
        .filter(ReorderRecommendation.id == recommendation_id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()

    quote = QuoteRecord(
        recommendation_id=recommendation_id,
        vendor_id=payload["vendor_id"],
        tenant_id=rec.tenant_id,
        quoted_price=payload["quoted_price"],
        lead_time_days=payload["lead_time_days"],
        proforma_blob_path=payload.get("proforma_blob_path"),
        received_at=datetime.utcnow(),
    )
    db.add(quote)
    rec.quotes_received = (rec.quotes_received or 0) + 1
    db.flush()

    all_quotes_in = (rec.quotes_received or 0) >= (rec.vendors_contacted or 1)

    if all_quotes_in:
        rec.status = "quotes_received"

        all_quotes = (
            db.query(QuoteRecord)
            .filter(QuoteRecord.recommendation_id == recommendation_id)
            .all()
        )
        quotes_with_vendors = []
        for q in all_quotes:
            v = db.query(Vendor).filter(Vendor.id == q.vendor_id).first()
            quotes_with_vendors.append({
                "vendor_id": q.vendor_id,
                "vendor_name": v.name if v else f"Vendor#{q.vendor_id}",
                "score": round(float(v.score), 1) if v else 0,
                "quoted_price": q.quoted_price,
                "lead_time_days": q.lead_time_days,
            })

        winner_name, reasoning = _ai_compare_quotes(
            sku_code=sku.sku_code if sku else "",
            quantity=rec.quantity,
            unit=sku.unit if sku else "units",
            quotes=quotes_with_vendors,
            tenant_id=rec.tenant_id,
            recommendation_id=recommendation_id,
        )

        for q_info in quotes_with_vendors:
            if q_info["vendor_name"] == winner_name:
                winning_vendor = (
                    db.query(Vendor)
                    .filter(Vendor.id == q_info["vendor_id"])
                    .first()
                )
                if winning_vendor:
                    rec.winning_vendor_id = winning_vendor.id
                break

        rec.ai_quote_reasoning = reasoning

    db.commit()
    return {
        "quote_stored": True,
        "quotes_received": rec.quotes_received,
        "vendors_contacted": rec.vendors_contacted,
        "all_quotes_in": all_quotes_in,
        "status": rec.status,
    }


def _ai_compare_quotes(
    sku_code: str,
    quantity: float,
    unit: str,
    quotes: list[dict],
    tenant_id: int = 1,
    recommendation_id: int = 0,
) -> tuple[str, str]:
    """
    Call GPT-OSS-120B to compare vendor quotes and return (winner_name, reasoning).
    Logs to Cosmos DB. Falls back to score-based selection on any error.
    """
    from openai import OpenAI
    from backend.integrations.cosmos_logger import log_quote_comparison

    quotes_text = "\n".join([
        f"- {q['vendor_name']}: Rs {q['quoted_price']}/{unit}, "
        f"{q['lead_time_days']} days lead time, reliability score {q['score']}/100"
        for q in quotes
    ])

    prompt = (
        f"You are a procurement AI for an Indian manufacturing company.\n"
        f"Compare these vendor quotes for {int(quantity)} {unit} of {sku_code}:\n\n"
        f"{quotes_text}\n\n"
        f"Consider: price per unit, lead time, and vendor reliability score.\n"
        f"Respond with valid JSON only, no markdown:\n"
        f'{{"winner": "<exact vendor name>", "reasoning": "<2-3 sentences>"}}'
    )

    try:
        client = OpenAI(
            base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
        )
        _t0 = time.time()
        response = client.chat.completions.create(
            model=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-oss-120b"),
            messages=[
                {"role": "system", "content": "You are a procurement AI. Respond only in valid JSON."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=0.1,
        )
        _latency_ms = int((time.time() - _t0) * 1000)

        raw = (response.choices[0].message.content or "").strip()
        clean = re.sub(r"```json|```", "", raw).strip()
        # Extract JSON object from reasoning model output
        match = re.search(r"\{.*\}", clean, re.DOTALL)
        if match:
            clean = match.group(0)
        data = json.loads(clean)
        winner = data["winner"]
        reasoning = data["reasoning"]

        log_quote_comparison(
            tenant_id=tenant_id,
            sku_code=sku_code,
            recommendation_id=recommendation_id,
            quotes_summary=quotes_text,
            winner=winner,
            reasoning=reasoning,
            latency_ms=_latency_ms,
            prompt_tokens=getattr(getattr(response, "usage", None), "prompt_tokens", 0),
            completion_tokens=getattr(getattr(response, "usage", None), "completion_tokens", 0),
        )
        return winner, reasoning

    except Exception as exc:
        logger.warning("GPT-OSS-120B quote comparison failed: %s — using score fallback", exc)
        best = max(quotes, key=lambda q: q["score"])
        return (
            best["vendor_name"],
            f"Selected {best['vendor_name']} based on highest reliability score "
            f"({best['score']}/100). AI comparison unavailable."
        )


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/recommendations/{id}/issue-po
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/recommendations/{recommendation_id}/issue-po")
def issue_po(
    recommendation_id: int,
    tenant_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Issue a Purchase Order to the winning vendor.
    Generates PO PDF, uploads to Blob, emails to TEST_EMAIL,
    triggers Vendor Simulator which generates Tax Invoice after 5s.
    """
    import httpx

    rec = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.id == recommendation_id,
            ReorderRecommendation.tenant_id == tenant_id,
        )
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    valid_pre_po = {"quotes_received", "pending_rohan", "pending_harpreet"}
    if rec.status not in valid_pre_po:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot issue PO from status '{rec.status}'"
        )

    sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()
    winning_vendor = db.query(Vendor).filter(Vendor.id == rec.winning_vendor_id).first()
    if not winning_vendor:
        raise HTTPException(status_code=400, detail="No winning vendor set")

    winning_quote = (
        db.query(QuoteRecord)
        .filter(
            QuoteRecord.recommendation_id == recommendation_id,
            QuoteRecord.vendor_id == winning_vendor.id,
        )
        .first()
    )
    unit_price = winning_quote.quoted_price if winning_quote else (
        (rec.order_value or 0) / rec.quantity if rec.quantity else 0
    )

    po_bytes = generate_purchase_order_pdf(
        rec_id=recommendation_id,
        sku_code=sku.sku_code if sku else "",
        vendor_name=winning_vendor.name,
        quantity=rec.quantity,
        unit_price=unit_price,
        unit=sku.unit if sku else "units",
        sku_description=sku.name if sku else "Materials",
    )

    year = datetime.utcnow().year
    po_blob_path = f"tenant-{tenant_id}/purchase-orders/PO-{year}-{recommendation_id:04d}.pdf"
    upload_document(po_blob_path, po_bytes)
    po_sas_url = get_sas_url(po_blob_path)

    po_number = f"PO-{year}-{recommendation_id:04d}"
    order_value = round(rec.quantity * unit_price, 2)

    # ── Spend policy gate (only for fresh quotes_received, not re-approvals) ────────────
    if rec.status == "quotes_received":
        from backend.integrations.spend_policy import evaluate_spend_policy
        policy_result = evaluate_spend_policy(
            tenant_id=tenant_id,
            recommendation_id=recommendation_id,
            sku_code=sku.sku_code if sku else "",
            sku_name=sku.name if sku else "Materials",
            vendor_name=winning_vendor.name,
            quantity=rec.quantity,
            unit=sku.unit if sku else "units",
            unit_price=unit_price,
            order_value=order_value,
            db=db,
        )

        if policy_result["decision"] != "approved":
            rec.order_value = order_value
            rec.po_blob_path = po_blob_path
            rec.po_number = po_number
            rec.status = policy_result["decision"]  # "pending_rohan" or "pending_harpreet"
            rec.ai_quote_reasoning = (
                (rec.ai_quote_reasoning or "")
                + f"\n\n[Spend Policy] {policy_result['reasoning']}"
            ).strip()
            db.commit()

            from backend.integrations.notification_service import send_spend_approval_request_email
            approver_name = "Harpreet" if policy_result["tier"] == "harpreet" else "Rohan"
            background_tasks.add_task(
                send_spend_approval_request_email,
                approver_name=approver_name,
                sku_code=sku.sku_code if sku else "",
                sku_name=sku.name if sku else "Materials",
                vendor_name=winning_vendor.name,
                quantity=rec.quantity,
                unit=sku.unit if sku else "units",
                order_value=order_value,
                po_number=po_number,
                recommendation_id=recommendation_id,
            )

            return {
                "po_issued": False,
                "status": policy_result["decision"],
                "policy_tier": policy_result["tier"],
                "order_value": order_value,
                "reasoning": policy_result["reasoning"],
                "message": (
                    "Order held for Rohan's approval"
                    if policy_result["tier"] == "rohan"
                    else "High-value order held for Harpreet's approval"
                ),
            }
    # ── Auto-approved or already approved by manager: proceed with PO issuance ─────

    rec.po_blob_path = po_blob_path
    rec.po_number = po_number
    rec.status = "po_issued"
    rec.order_value = order_value
    db.commit()

    from backend.integrations.notification_service import send_po_email
    background_tasks.add_task(
        send_po_email,
        sku_code=sku.sku_code if sku else "",
        vendor_name=winning_vendor.name,
        quantity=rec.quantity,
        unit=sku.unit if sku else "units",
        po_number=po_number,
        po_sas_url=po_sas_url,
        order_value=rec.order_value or 0.0,
        recommendation_id=recommendation_id,
    )

    simulator_url = os.environ.get("VENDOR_SIMULATOR_URL", "http://localhost:8001")
    try:
        with httpx.Client(timeout=5.0) as client:
            client.post(
                f"{simulator_url}/po-received",
                json={
                    "rec_id": recommendation_id,
                    "vendor_name": winning_vendor.name,
                    "sku_code": sku.sku_code if sku else "",
                    "sku_description": sku.name if sku else "Materials",
                    "quantity": rec.quantity,
                    "unit_price": unit_price,
                    "unit": sku.unit if sku else "units",
                    "po_number": po_number,
                    "tenant_id": tenant_id,
                },
            )
    except Exception as exc:
        logger.warning("Vendor Simulator not reachable for PO: %s", exc)

    return {
        "po_issued": True,
        "po_number": po_number,
        "vendor": winning_vendor.name,
        "order_value": rec.order_value,
        "blob_path": po_blob_path,
        "view_url": po_sas_url,
    }


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/recommendations/{id}/invoice-received  (called by Vendor Simulator)
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/recommendations/{recommendation_id}/invoice-received")
def invoice_received(
    recommendation_id: int,
    payload: dict,
    db: Session = Depends(get_db),
):
    """
    Called by Vendor Simulator when tax invoice is ready.
    Stores invoice blob path, creates SpendRecord, marks order complete.
    """
    from backend.models import SpendRecord

    rec = (
        db.query(ReorderRecommendation)
        .filter(ReorderRecommendation.id == recommendation_id)
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    blob_path = payload.get("blob_path") or None  # treat empty string as None
    rec.invoice_blob_path = blob_path
    rec.invoice_number = payload["invoice_number"]
    rec.status = "invoice_received"

    now = datetime.utcnow()
    spend = SpendRecord(
        tenant_id=rec.tenant_id,
        vendor_id=rec.winning_vendor_id or rec.vendor_id,
        sku_id=rec.sku_id,
        quantity=rec.quantity,
        unit_price=payload["total_with_gst"] / rec.quantity if rec.quantity else 0,
        total_value=payload["total_with_gst"],
        month=now.month,
        year=now.year,
        created_at=now,
    )
    db.add(spend)
    db.commit()

    return {
        "invoice_received": True,
        "invoice_number": payload["invoice_number"],
        "status": rec.status,
    }


# ──────────────────────────────────────────────────────────────────────────────
# GET /agents/orders  — procurement pipeline view
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/orders")
def get_orders(tenant_id: int, db: Session = Depends(get_db)):
    """
    Returns all recommendations in the procurement pipeline
    (rfq_sent through invoice_received), with quotes and blob URLs.
    """
    pipeline_statuses = [
        "rfq_sent", "quotes_received", "po_issued", "invoice_received"
    ]
    recs = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.tenant_id == tenant_id,
            ReorderRecommendation.status.in_(pipeline_statuses),
        )
        .order_by(ReorderRecommendation.created_at.desc())
        .all()
    )

    result = []
    for rec in recs:
        sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()
        winning_vendor = (
            db.query(Vendor).filter(Vendor.id == rec.winning_vendor_id).first()
            if rec.winning_vendor_id else None
        )
        quotes = db.query(QuoteRecord).filter(
            QuoteRecord.recommendation_id == rec.id
        ).all()
        quotes_data = []
        for q in quotes:
            qv = db.query(Vendor).filter(Vendor.id == q.vendor_id).first()
            quotes_data.append({
                "vendor_name": qv.name if qv else f"Vendor#{q.vendor_id}",
                "vendor_id": q.vendor_id,
                "quoted_price": q.quoted_price,
                "lead_time_days": q.lead_time_days,
                "score": round(float(qv.score), 1) if qv else 0,
                "proforma_url": get_sas_url(q.proforma_blob_path) if q.proforma_blob_path else None,
                "is_winner": q.vendor_id == rec.winning_vendor_id,
            })

        result.append({
            "id": rec.id,
            "sku_code": sku.sku_code if sku else None,
            "sku_name": sku.name if sku else None,
            "quantity": rec.quantity,
            "unit": sku.unit if sku else "units",
            "status": rec.status,
            "order_value": rec.order_value,
            "vendors_contacted": rec.vendors_contacted,
            "quotes_received": rec.quotes_received,
            "winning_vendor": winning_vendor.name if winning_vendor else None,
            "ai_reasoning": rec.ai_quote_reasoning,
            "po_number": rec.po_number,
            "invoice_number": rec.invoice_number,
            "po_url": get_sas_url(rec.po_blob_path) if rec.po_blob_path else None,
            "invoice_url": get_sas_url(rec.invoice_blob_path) if rec.invoice_blob_path else None,
            "quotes": quotes_data,
            "created_at": rec.created_at.isoformat(),
        })

    return result


# ──────────────────────────────────────────────────────────────────────────────
# GET /agents/ai-log  — Cosmos AI audit log
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/ai-log")
def get_ai_log_endpoint(tenant_id: int, limit: int = 50):
    """
    Returns recent AI reasoning log entries from Cosmos DB.
    Includes watchdog, quote_comparison, and policy_evaluation entries.
    Returns [] gracefully if Cosmos is not configured.
    """
    from backend.integrations.cosmos_logger import get_ai_log
    return get_ai_log(tenant_id=tenant_id, limit=limit)


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/recommendations/{id}/approve-spend
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/recommendations/{recommendation_id}/approve-spend")
def approve_spend(
    recommendation_id: int,
    tenant_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Rohan (or Harpreet) approves a spend-held PO.
    Validates the rec is pending_rohan or pending_harpreet, then
    delegates directly to issue_po — which skips policy re-evaluation
    for those statuses and proceeds straight to PDF/email/simulator.
    """
    rec = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.id == recommendation_id,
            ReorderRecommendation.tenant_id == tenant_id,
        )
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if rec.status not in ("pending_rohan", "pending_harpreet"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve spend from status '{rec.status}'",
        )
    # issue_po accepts pending_rohan/pending_harpreet and skips policy re-evaluation
    return issue_po(
        recommendation_id=recommendation_id,
        tenant_id=tenant_id,
        background_tasks=background_tasks,
        db=db,
    )


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/recommendations/{id}/reject-spend
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/recommendations/{recommendation_id}/reject-spend")
def reject_spend(
    recommendation_id: int,
    tenant_id: int,
    approver: str = "rohan",
    reason: str = "",
    db: Session = Depends(get_db),
):
    """
    Rohan (or Harpreet) rejects a spend-held PO.
    Sets status='rejected', writes rejection feedback to Cosmos for the
    AI feedback loop (same path as rejecting a watchdog recommendation).
    """
    rec = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.id == recommendation_id,
            ReorderRecommendation.tenant_id == tenant_id,
        )
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if rec.status not in ("pending_rohan", "pending_harpreet"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject spend from status '{rec.status}'",
        )

    sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()
    sku_code = sku.sku_code if sku else f"SKU#{rec.sku_id}"
    order_value = rec.order_value or 0.0

    rec.status = "rejected"
    db.commit()

    from backend.integrations.cosmos_logger import write_rejection_feedback
    feedback_text = reason.strip() if reason.strip() else (
        f"{approver.capitalize()} rejected spend approval for {sku_code} — "
        f"order value Rs {order_value:,.0f}. No reason provided."
    )
    write_rejection_feedback(
        tenant_id=tenant_id,
        recommendation_id=recommendation_id,
        sku_code=sku_code,
        reason=feedback_text,
    )

    return {"rejected": True, "recommendation_id": recommendation_id, "sku_code": sku_code}


# ──────────────────────────────────────────────────────────────────────────────
# GET /agents/spend-policy   — read current thresholds
# PUT /agents/spend-policy   — upsert thresholds
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/spend-policy")
def get_spend_policy(tenant_id: int, db: Session = Depends(get_db)):
    """
    Returns the current spend-approval thresholds for the tenant.
    Falls back to env-var defaults (AUTO_LIMIT / ROHAN_LIMIT) if no DB row exists.
    """
    from backend.integrations.spend_policy import get_policy
    auto_limit, rohan_limit = get_policy(tenant_id, db)

    policy_row = db.query(SpendPolicy).filter_by(tenant_id=tenant_id).first()
    return {
        "tenant_id": tenant_id,
        "auto_approve_limit": auto_limit,
        "rohan_limit": rohan_limit,
        "updated_at": policy_row.updated_at.isoformat() if policy_row else None,
        "source": "database" if policy_row else "env_defaults",
    }


@router.put("/spend-policy")
def update_spend_policy(
    tenant_id: int,
    auto_approve_limit: float,
    rohan_limit: float,
    db: Session = Depends(get_db),
):
    """
    Upsert spend-approval thresholds for the tenant.
    Creates a new row if one doesn't exist, otherwise updates in place.
    """
    if auto_approve_limit <= 0 or rohan_limit <= 0:
        raise HTTPException(status_code=400, detail="Limits must be positive values")
    if auto_approve_limit >= rohan_limit:
        raise HTTPException(
            status_code=400,
            detail="auto_approve_limit must be less than rohan_limit",
        )

    policy = db.query(SpendPolicy).filter_by(tenant_id=tenant_id).first()
    if policy:
        policy.auto_approve_limit = auto_approve_limit
        policy.rohan_limit = rohan_limit
        policy.updated_at = datetime.utcnow()
    else:
        policy = SpendPolicy(
            tenant_id=tenant_id,
            auto_approve_limit=auto_approve_limit,
            rohan_limit=rohan_limit,
            updated_at=datetime.utcnow(),
        )
        db.add(policy)

    db.commit()
    return {
        "tenant_id": tenant_id,
        "auto_approve_limit": float(policy.auto_approve_limit),
        "rohan_limit": float(policy.rohan_limit),
        "updated_at": policy.updated_at.isoformat(),
    }


# ──────────────────────────────────────────────────────────────────────────────
# GET /agents/rfq-inbox
# Returns recs in status="rfq_sent" where Punjab Components House (Meena's
# vendor) has NOT yet submitted a quote.  Meena's dashboard polls this.
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/rfq-inbox")
def get_rfq_inbox(tenant_id: int, db: Session = Depends(get_db)):
    """
    Meena's pending RFQ inbox.

    Returns recommendations that are waiting for Meena's quote — i.e. status
    is "rfq_sent" and Punjab Components House has not yet posted a QuoteRecord
    for that recommendation.
    """
    # Find Punjab's vendor_id for this tenant
    punjab = (
        db.query(Vendor)
        .filter(
            Vendor.tenant_id == tenant_id,
            Vendor.name.ilike("%Punjab%"),
        )
        .first()
    )

    rfq_sent_recs = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.tenant_id == tenant_id,
            ReorderRecommendation.status == "rfq_sent",
        )
        .order_by(ReorderRecommendation.created_at.desc())
        .all()
    )

    result = []
    for rec in rfq_sent_recs:
        # If Punjab exists and has already quoted, skip this rec
        if punjab:
            already_quoted = (
                db.query(QuoteRecord)
                .filter(
                    QuoteRecord.recommendation_id == rec.id,
                    QuoteRecord.vendor_id == punjab.id,
                )
                .first()
            )
            if already_quoted:
                continue

        sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()
        result.append({
            "recommendation_id": rec.id,
            "sku_code": sku.sku_code if sku else None,
            "sku_name": sku.name if sku else None,
            "quantity": rec.quantity,
            "unit": sku.unit if sku else None,
            "vendors_contacted": rec.vendors_contacted,
            "quotes_received": rec.quotes_received or 0,
            "created_at": rec.created_at.isoformat(),
        })

    return result


# ──────────────────────────────────────────────────────────────────────────────
# GET /agents/pending-spend-approvals
# Returns recs in status="pending_rohan" or "pending_harpreet" with enough
# detail to render Rohan's approval card in the frontend.
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/pending-spend-approvals")
def get_pending_spend_approvals(tenant_id: int, db: Session = Depends(get_db)):
    """
    Rohan's spend-approval queue.

    Returns all recommendations currently held for spend approval, with the
    order value, policy tier, winning vendor, and PO number so the frontend
    can render a complete approval card without extra fetches.
    """
    recs = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.tenant_id == tenant_id,
            ReorderRecommendation.status.in_(["pending_rohan", "pending_harpreet"]),
        )
        .order_by(ReorderRecommendation.created_at.desc())
        .all()
    )

    result = []
    for rec in recs:
        sku = db.query(SKU).filter(SKU.id == rec.sku_id).first()
        vendor = db.query(Vendor).filter(Vendor.id == rec.vendor_id).first()
        result.append({
            "recommendation_id": rec.id,
            "sku_code": sku.sku_code if sku else None,
            "sku_name": sku.name if sku else None,
            "quantity": rec.quantity,
            "unit": sku.unit if sku else None,
            "vendor_name": vendor.name if vendor else None,
            "order_value": rec.order_value,
            "policy_tier": "harpreet" if rec.status == "pending_harpreet" else "rohan",
            "status": rec.status,
            "po_number": rec.po_number,
            "created_at": rec.created_at.isoformat(),
        })

    return result


# ──────────────────────────────────────────────────────────────────────────────
# POST /agents/chat — ChainFlow Copilot natural language Q&A
# ──────────────────────────────────────────────────────────────────────────────

@router.post("/chat")
def copilot_chat(payload: dict, tenant_id: int = 1, db: Session = Depends(get_db)):
    """
    Natural language copilot for Rohan.
    Fetches live context from DB (SKU stock levels, vendors, pending recs),
    builds a system prompt with the data, calls gpt-oss-120b, returns the answer.
    """
    from openai import OpenAI
    import time as _time

    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")

    # Gather live context
    all_skus = db.query(SKU).filter(SKU.tenant_id == tenant_id).all()
    sku_lines = "\n".join(
        f"  {s.sku_code}: stock {s.current_quantity}/{s.reorder_threshold} {s.unit} "
        f"({'CRITICAL' if s.current_quantity < s.reorder_threshold * 0.3 else 'LOW' if s.current_quantity < s.reorder_threshold else 'OK'})"
        for s in all_skus
    )

    all_vendors = db.query(Vendor).filter(Vendor.tenant_id == tenant_id).all()
    vendor_lines = "\n".join(
        f"  {v.name}: score {round(float(v.score), 1)}, "
        f"{v.total_orders} orders, "
        f"{round(v.on_time_deliveries / v.total_orders * 100, 1) if v.total_orders else 0}% on-time"
        for v in all_vendors
    )

    pending_recs = (
        db.query(ReorderRecommendation)
        .filter(
            ReorderRecommendation.tenant_id == tenant_id,
            ReorderRecommendation.status.in_(["pending", "pending_rohan", "pending_harpreet", "no_vendor"]),
        )
        .all()
    )
    pending_lines = "\n".join(
        f"  Rec#{r.id} "
        f"{(db.query(SKU).filter(SKU.id == r.sku_id).first() or SKU(sku_code='?')).sku_code}: "
        f"{r.status}"
        for r in pending_recs
    ) or "  None"

    system_prompt = (
        "You are ChainFlow Copilot — an AI assistant for Harpreet Hosiery Works, a garment manufacturer in Ludhiana.\n"
        "You help Rohan (procurement manager) answer questions about inventory, vendors, and spending.\n"
        "Answer concisely in 1-3 sentences. Use Indian number formatting. Do not hallucinate.\n\n"
        f"LIVE DATA (as of right now):\n\n"
        f"SKU Stock Levels:\n{sku_lines}\n\n"
        f"Vendor Scorecards:\n{vendor_lines}\n\n"
        f"Pending Actions:\n{pending_lines}\n"
    )

    client = OpenAI(
        base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
    )
    t0 = _time.time()
    response = client.chat.completions.create(
        model=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-oss-120b"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": question},
        ],
        max_tokens=300,
        temperature=0.2,
    )
    latency_ms = int((_time.time() - t0) * 1000)
    answer = (response.choices[0].message.content or "").strip()

    return {
        "answer":     answer,
        "latency_ms": latency_ms,
        "model":      os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-oss-120b"),
    }
