"""
ChainFlow — vendor_simulator/main.py
Simulates vendor responses for the RFQ → Proforma → PO → Tax Invoice flow.

Runs as a separate FastAPI service on port 8001.
Receives webhooks from ChainFlow and calls back with quotes / invoices.

Start:
    uvicorn vendor_simulator.main:app --port 8001 --reload

Environment (same .env file as main ChainFlow):
    CHAINFLOW_API_URL       — http://localhost:8000
    AZURE_STORAGE_CONNECTION_STRING
    AZURE_STORAGE_CONTAINER
    ACS_CONNECTION_STRING
    ACS_EMAIL_SENDER
    TEST_EMAIL
"""

import asyncio
import logging
import os
import sys

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI

# ── Allow running from the chainflow root dir ──────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

logger = logging.getLogger("vendor_simulator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(name)s  %(message)s")

app = FastAPI(title="ChainFlow Vendor Simulator", version="1.0.0")

# ──────────────────────────────────────────────────────────────────────────────
# Vendor configuration
# price_multiplier:  applied to each vendor's base_price from the RFQ payload
# extra_lead_days:   added to vendor's base lead_time_days
# ──────────────────────────────────────────────────────────────────────────────
VENDOR_CONFIG = {
    "Punjab Textile Suppliers": {"price_multiplier": 1.00, "extra_lead_days": 0,  "delay": 3},
    "Sharma & Sons Traders":    {"price_multiplier": 1.12, "extra_lead_days": 3,  "delay": 5},
    "Gupta Enterprises":        {"price_multiplier": 0.95, "extra_lead_days": 7,  "delay": 8},
}
DEFAULT_CONFIG = {"price_multiplier": 1.05, "extra_lead_days": 2, "delay": 4}


def _vendor_cfg(name: str) -> dict:
    """Return config for this vendor, with a fallback for unknown vendors."""
    for key, cfg in VENDOR_CONFIG.items():
        if key.lower() in name.lower() or name.lower() in key.lower():
            return cfg
    return DEFAULT_CONFIG


# ──────────────────────────────────────────────────────────────────────────────
# POST /rfq-received — ChainFlow calls this after send_rfq
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/rfq-received")
async def rfq_received(payload: dict):
    """
    Receive RFQ from ChainFlow. Schedule staggered vendor responses.
    Each vendor: generate proforma PDF → upload blob → call /quote-received.
    """
    logger.info("RFQ received for rec_id=%s  sku=%s  vendors=%d",
                payload.get("rec_id"), payload.get("sku_code"), len(payload.get("vendors", [])))
    asyncio.create_task(simulate_vendor_responses(payload))
    return {"accepted": True, "vendors": len(payload.get("vendors", []))}


# ──────────────────────────────────────────────────────────────────────────────
# POST /po-received — ChainFlow calls this after issue_po
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/po-received")
async def po_received(payload: dict):
    """
    Receive PO from ChainFlow. After 5s, generate tax invoice and call /invoice-received.
    """
    logger.info("PO received: %s  from %s", payload.get("po_number"), payload.get("vendor_name"))
    asyncio.create_task(generate_and_send_invoice(payload))
    return {"accepted": True}


# ──────────────────────────────────────────────────────────────────────────────
# Async helpers
# ──────────────────────────────────────────────────────────────────────────────

async def simulate_vendor_responses(rfq: dict):
    """
    For each vendor in the RFQ, wait `delay` seconds then:
    1. Calculate quoted price using multiplier
    2. Generate proforma PDF
    3. Upload PDF to blob storage
    4. Send proforma email
    5. POST quote to ChainFlow /quote-received
    """
    rec_id      = rfq["rec_id"]
    sku_code    = rfq["sku_code"]
    sku_desc    = rfq.get("sku_description", "Supply of materials")
    quantity    = rfq["quantity"]
    unit        = rfq.get("unit", "units")
    tenant_id   = rfq.get("tenant_id", 1)
    vendors     = rfq.get("vendors", [])

    for vendor in vendors:
        cfg         = _vendor_cfg(vendor["name"])
        delay       = cfg["delay"]
        base_price  = vendor.get("base_price", 100)
        unit_price  = round(base_price * cfg["price_multiplier"], 2)
        lead_days   = vendor.get("lead_time_days", 7) + cfg["extra_lead_days"]

        logger.info("  [%s] responding in %ds  price=%.2f  lead=%d days",
                    vendor["name"], delay, unit_price, lead_days)
        await asyncio.sleep(delay)

        blob_path = await _send_proforma(
            rec_id=rec_id,
            tenant_id=tenant_id,
            vendor_id=vendor["id"],
            vendor_name=vendor["name"],
            sku_code=sku_code,
            sku_desc=sku_desc,
            quantity=quantity,
            unit=unit,
            unit_price=unit_price,
            lead_days=lead_days,
        )

        chainflow_url = os.environ.get("CHAINFLOW_API_URL", "http://localhost:8000")
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{chainflow_url}/agents/recommendations/{rec_id}/quote-received",
                    json={
                        "vendor_id": vendor["id"],
                        "vendor_name": vendor["name"],
                        "quoted_price": unit_price,
                        "lead_time_days": lead_days,
                        "proforma_blob_path": blob_path,
                    },
                )
            logger.info("  [%s] quote posted → %d", vendor["name"], resp.status_code)
        except Exception as exc:
            logger.error("  [%s] failed to post quote: %s", vendor["name"], exc)


async def generate_and_send_invoice(po: dict):
    """
    Wait 5 seconds after PO receipt, then:
    1. Generate tax invoice PDF (price × 1.18 GST)
    2. Upload to blob storage
    3. Send invoice email
    4. POST to ChainFlow /invoice-received
    """
    await asyncio.sleep(5)

    rec_id      = po["rec_id"]
    vendor_name = po["vendor_name"]
    sku_code    = po.get("sku_code", "")
    sku_desc    = po.get("sku_description", "Materials")
    quantity    = po["quantity"]
    unit_price  = po["unit_price"]
    unit        = po.get("unit", "units")
    po_number   = po["po_number"]
    tenant_id   = po.get("tenant_id", 1)

    total_with_gst = round(quantity * unit_price * 1.18, 2)
    invoice_number = f"INV-{po_number}"
    blob_path = None

    try:
        from backend.integrations.po_generator import generate_tax_invoice_pdf
        from backend.integrations.blob_storage import upload_document

        pdf_bytes = generate_tax_invoice_pdf(
            rec_id=rec_id,
            sku_code=sku_code,
            vendor_name=vendor_name,
            quantity=quantity,
            unit_price=unit_price,
            unit=unit,
            invoice_number=invoice_number,
            po_number=po_number,
            sku_description=sku_desc,
        )

        from datetime import datetime
        year = datetime.utcnow().year
        blob_path = f"tenant-{tenant_id}/tax-invoices/{invoice_number}.pdf"
        upload_document(blob_path, pdf_bytes)
        logger.info("Tax invoice PDF uploaded: %s", blob_path)

    except Exception as exc:
        logger.error("Failed to generate/upload tax invoice: %s", exc)

    # Send invoice email
    try:
        from backend.integrations.notification_service import send_invoice_email
        send_invoice_email(
            sku_code=sku_code,
            vendor_name=vendor_name,
            invoice_number=invoice_number,
            total_with_gst=total_with_gst,
            recommendation_id=rec_id,
            quantity=quantity,
            unit_price=unit_price,
            unit=unit,
            po_number=po_number,
        )
    except Exception:
        pass  # email is best-effort

    chainflow_url = os.environ.get("CHAINFLOW_API_URL", "http://localhost:8000")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{chainflow_url}/agents/recommendations/{rec_id}/invoice-received",
                json={
                    "vendor_name": vendor_name,
                    "blob_path": blob_path or "",
                    "invoice_number": invoice_number,
                    "total_with_gst": total_with_gst,
                },
            )
        logger.info("Invoice posted to ChainFlow → %d", resp.status_code)
    except Exception as exc:
        logger.error("Failed to post invoice to ChainFlow: %s", exc)


async def _send_proforma(
    rec_id, tenant_id, vendor_id, vendor_name,
    sku_code, sku_desc, quantity, unit, unit_price, lead_days
) -> str | None:
    """
    Generate proforma PDF, upload blob, send email. Returns blob_path or None.
    """
    blob_path = None
    try:
        from backend.integrations.po_generator import generate_proforma_pdf
        from backend.integrations.blob_storage import upload_document

        pdf_bytes = generate_proforma_pdf(
            rec_id=rec_id,
            sku_code=sku_code,
            vendor_name=vendor_name,
            quantity=quantity,
            unit_price=unit_price,
            unit=unit,
            lead_time_days=lead_days,
            sku_description=sku_desc,
        )

        blob_path = f"tenant-{tenant_id}/proformas/PFMA-{rec_id:04d}-{vendor_id}.pdf"
        upload_document(blob_path, pdf_bytes)
        logger.info("  Proforma uploaded: %s", blob_path)

    except Exception as exc:
        logger.error("  Proforma generation failed for %s: %s", vendor_name, exc)

    # Best-effort email — include real PDF download link
    sas_url = None
    try:
        if blob_path:
            from backend.integrations.blob_storage import get_sas_url
            sas_url = get_sas_url(blob_path)
    except Exception:
        pass

    try:
        from backend.integrations.notification_service import send_proforma_email
        send_proforma_email(
            sku_code=sku_code,
            vendor_name=vendor_name,
            quantity=quantity,
            unit=unit,
            unit_price=unit_price,
            lead_days=lead_days,
            recommendation_id=rec_id,
            proforma_sas_url=sas_url,
        )
    except Exception:
        pass

    return blob_path
